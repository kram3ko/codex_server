"""Per-chat Codex session store.

Each chat (TG chat_id) keeps a long-lived `CodexClient` plus the DB ids of
the User/Chat row so handlers can persist messages and emit events without
re-resolving them on every turn.

Codex thread state in-memory у sidecar — id тримаємо також у `chats.codex_thread_id`
для token-економії: при відкритті сесії читаємо stored id, валідація відкладена
до першого turn (optimistic retry на `-32600 thread not found`).
"""

import asyncio
import contextlib
from dataclasses import dataclass, field

import structlog

from app.config import settings
from app.db.base import SessionLocal
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
    # instead of opening a fresh turn.
    steer_pending: bool = False


class ChatSessionStore:
    def __init__(self) -> None:
        self._sessions: dict[int, ChatSession] = {}
        self._lock = asyncio.Lock()

    async def get_or_open(
        self,
        tg_user_id: int,
        tg_chat_id: int,
        display_name: str | None = None,
    ) -> ChatSession:
        async with self._lock:
            existing = self._sessions.get(tg_chat_id)
            if existing is not None:
                return existing

            db_user_id, db_chat_id, stored_thread_id = await self._bootstrap_db(
                tg_user_id, tg_chat_id, display_name,
            )
            initial = stored_thread_id if settings.CODEX_THREAD_REUSE_ENABLED else None
            client = await self._open_codex_client(db_chat_id, initial)
            session = ChatSession(
                client=client,
                db_chat_id=db_chat_id,
                db_user_id=db_user_id,
            )
            self._sessions[tg_chat_id] = session
            log.info(
                "tg_session_opened",
                tg_chat_id=tg_chat_id,
                db_chat_id=db_chat_id,
                db_user_id=db_user_id,
                rehydrated_thread=stored_thread_id is not None,
            )
            return session

    async def get(self, tg_chat_id: int) -> ChatSession | None:
        async with self._lock:
            return self._sessions.get(tg_chat_id)

    async def reset(self, tg_chat_id: int) -> bool:
        """Drop in-memory session + clear stored Codex thread id."""
        async with self._lock:
            session = self._sessions.pop(tg_chat_id, None)
        if session is None:
            return False
        with contextlib.suppress(Exception):
            await session.client.close()
        async with SessionLocal() as db:
            await chat_service.set_codex_thread_id(db, session.db_chat_id, None)
            await db.commit()
        log.info("tg_session_reset", tg_chat_id=tg_chat_id)
        return True

    async def close_all(self) -> None:
        async with self._lock:
            sessions = list(self._sessions.values())
            self._sessions.clear()
        if not sessions:
            return
        async with asyncio.TaskGroup() as tg:
            for session in sessions:
                tg.create_task(self._close_one(session))

    @staticmethod
    async def _close_one(session: ChatSession) -> None:
        with contextlib.suppress(Exception):
            await session.client.close()

    @staticmethod
    async def _bootstrap_db(
        tg_user_id: int,
        tg_chat_id: int,
        display_name: str | None,
    ) -> tuple[int, int, str | None]:
        async with SessionLocal() as db:
            user = await user_service.get_or_create_by_tg(db, tg_user_id, display_name)
            chat = await chat_service.get_or_create_for_tg(db, user.id, tg_chat_id)
            stored_thread_id = chat.codex_thread_id
            await db.commit()
            return user.id, chat.id, stored_thread_id

    @staticmethod
    async def _open_codex_client(
        db_chat_id: int,
        initial_thread_id: str | None,
    ) -> CodexClient:
        async def _persist_thread(new_thread_id: str | None) -> None:
            async with SessionLocal() as db:
                await chat_service.set_codex_thread_id(db, db_chat_id, new_thread_id)
                await db.commit()

        client = CodexClient(
            url=settings.CODEX_APP_SERVER_URL,
            cwd=settings.CODEX_CWD,
            approval_policy=settings.CODEX_APPROVAL_POLICY,
            sandbox=settings.CODEX_SANDBOX,
            request_timeout=settings.CODEX_REQUEST_TIMEOUT_SECONDS,
            initial_thread_id=initial_thread_id,
            on_thread_change=_persist_thread,
        )
        await client.connect()
        return client
