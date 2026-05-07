"""Per-chat Codex session store.

Holds a long-lived `CodexClient` per TG chat plus the DB ids of the
User/Chat row so handlers persist messages and emit events without
re-resolving them on every turn.

Concurrency:
- `_sessions` map keys live behind `_lock`. The lock guards ONLY the dict
  mutation, not the long-running WS connect — concurrent opens for
  different chats don't serialize on each other.
- Per-chat first-call wins via `asyncio.Future`: parallel callers for the
  same chat all `await` the same future the creator fills. Retries become
  possible after a failure (the future is dropped from the map).
"""

import asyncio
import contextlib
from dataclasses import dataclass, field

import structlog

from app.config import settings
from app.db.base import SessionLocal
from app.models import UserRole
from app.services.chats.default import chat_service
from app.services.codex.client import CodexClient
from app.services.users.default import user_service

log = structlog.get_logger(__name__)


@dataclass(slots=True)
class ChatSession:
    client: CodexClient
    db_chat_id: int
    db_user_id: int
    turn_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    current_turn_task: asyncio.Task | None = None
    # Set by on_callback("turn:steer"); next user message goes to turn/steer
    # instead of opening a fresh turn. Read-and-clear via `consume_steer()`.
    steer_pending: bool = False

    def consume_steer(self) -> bool:
        """Atomic test-and-clear of `steer_pending`.

        Sync method between awaits → asyncio scheduler can't preempt it,
        so two concurrent handlers can't both observe `True`.
        """
        if not self.steer_pending:
            return False
        self.steer_pending = False
        return True


class ChatSessionStore:
    def __init__(self) -> None:
        self._sessions: dict[int, asyncio.Future[ChatSession]] = {}
        self._lock = asyncio.Lock()

    async def get_or_open(
        self,
        tg_user_id: int,
        tg_chat_id: int,
        display_name: str | None = None,
    ) -> ChatSession:
        future, is_creator = await self._claim_slot(tg_chat_id)
        if is_creator:
            await self._fulfil_slot(future, tg_chat_id, tg_user_id, display_name)
        return await future

    async def get(self, tg_chat_id: int) -> ChatSession | None:
        async with self._lock:
            future = self._sessions.get(tg_chat_id)
        if future is None or not future.done():
            return None
        if future.exception() is not None:
            return None
        return future.result()

    async def reset(self, tg_chat_id: int) -> bool:
        """Drop in-memory session + clear stored Codex thread id."""
        async with self._lock:
            future = self._sessions.pop(tg_chat_id, None)
        if future is None:
            return False
        session = await self._safe_resolve(future)
        if session is None:
            return False
        await self._close_one(session)
        await self._clear_persisted_thread(session.db_chat_id)
        log.info("tg_session_reset", tg_chat_id=tg_chat_id)
        return True

    async def close_all(self) -> None:
        async with self._lock:
            futures = list(self._sessions.values())
            self._sessions.clear()
        if not futures:
            return
        async with asyncio.TaskGroup() as tg:
            for future in futures:
                tg.create_task(self._close_future(future))

    async def _claim_slot(
        self,
        tg_chat_id: int,
    ) -> tuple[asyncio.Future[ChatSession], bool]:
        async with self._lock:
            existing = self._sessions.get(tg_chat_id)
            if existing is not None and not _failed_attempt(existing):
                return existing, False
            future: asyncio.Future[ChatSession] = (
                asyncio.get_running_loop().create_future()
            )
            self._sessions[tg_chat_id] = future
            return future, True

    async def _fulfil_slot(
        self,
        future: asyncio.Future[ChatSession],
        tg_chat_id: int,
        tg_user_id: int,
        display_name: str | None,
    ) -> None:
        try:
            session = await self._build_session(tg_user_id, tg_chat_id, display_name)
        except Exception as exc:
            async with self._lock:
                if self._sessions.get(tg_chat_id) is future:
                    self._sessions.pop(tg_chat_id)
            future.set_exception(exc)
            raise
        future.set_result(session)

    async def _build_session(
        self,
        tg_user_id: int,
        tg_chat_id: int,
        display_name: str | None,
    ) -> ChatSession:
        db_user_id, db_chat_id, stored_thread_id, is_admin = await self._bootstrap_db(
            tg_user_id, tg_chat_id, display_name,
        )
        initial = stored_thread_id if settings.CODEX_THREAD_REUSE_ENABLED else None
        client = await self._open_codex_client(db_chat_id, initial, is_admin=is_admin)
        log.info(
            "tg_session_opened",
            tg_chat_id=tg_chat_id,
            db_chat_id=db_chat_id,
            db_user_id=db_user_id,
            is_admin=is_admin,
            rehydrated_thread=stored_thread_id is not None,
        )
        return ChatSession(
            client=client,
            db_chat_id=db_chat_id,
            db_user_id=db_user_id,
        )

    @staticmethod
    async def _close_one(session: ChatSession) -> None:
        with contextlib.suppress(Exception):
            await session.client.close()

    @classmethod
    async def _close_future(cls, future: asyncio.Future[ChatSession]) -> None:
        session = await cls._safe_resolve(future)
        if session is not None:
            await cls._close_one(session)

    @staticmethod
    async def _safe_resolve(future: asyncio.Future[ChatSession]) -> ChatSession | None:
        try:
            return await future
        except Exception as exc:  # noqa: BLE001
            log.warning("tg_session_resolve_failed", error=str(exc))
            return None

    @staticmethod
    async def _clear_persisted_thread(db_chat_id: int) -> None:
        async with SessionLocal() as db:
            await chat_service.set_codex_thread_id(db, db_chat_id, None)
            await db.commit()

    @staticmethod
    async def _bootstrap_db(
        tg_user_id: int,
        tg_chat_id: int,
        display_name: str | None,
    ) -> tuple[int, int, str | None, bool]:
        async with SessionLocal() as db:
            user = await user_service.get_or_create_by_tg(db, tg_user_id, display_name)
            chat = await chat_service.get_or_create_for_tg(db, user.id, tg_chat_id)
            stored_thread_id = chat.codex_thread_id
            is_admin = user.role == UserRole.ADMIN
            await db.commit()
            return user.id, chat.id, stored_thread_id, is_admin

    @staticmethod
    async def _open_codex_client(
        db_chat_id: int,
        initial_thread_id: str | None,
        *,
        is_admin: bool,
    ) -> CodexClient:
        async def _persist_thread(new_thread_id: str | None) -> None:
            async with SessionLocal() as db:
                await chat_service.set_codex_thread_id(db, db_chat_id, new_thread_id)
                await db.commit()

        url = settings.CODEX_APP_SERVER_URL if is_admin else settings.CODEX_GUEST_APP_SERVER_URL
        client = CodexClient(
            url=url,
            cwd=settings.CODEX_CWD,
            approval_policy=settings.CODEX_APPROVAL_POLICY,
            sandbox=settings.CODEX_SANDBOX,
            request_timeout=settings.CODEX_REQUEST_TIMEOUT_SECONDS,
            initial_thread_id=initial_thread_id,
            on_thread_change=_persist_thread,
            reasoning_effort=settings.CODEX_REASONING_EFFORT,
        )
        await client.connect()
        return client


def _failed_attempt(future: asyncio.Future[ChatSession]) -> bool:
    """A done future с exception лишається у мапі тільки до наступного claim_slot."""
    return future.done() and future.exception() is not None
