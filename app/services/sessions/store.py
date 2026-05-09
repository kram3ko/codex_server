"""Generic Codex session store for long-lived chat surfaces.

Owns a per-key `CodexClient`, persisted thread id wiring, quarantine checks,
history replay, and in-flight turn cancellation. Surface-specific code only
bootstraps DB identity and optionally records interrupt events.
"""

import asyncio
import contextlib
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import structlog

from app.config import settings
from app.db.base import SessionLocal
from app.models import MessageRole
from app.services.cache.default import cache
from app.services.chats.default import chat_service
from app.services.codex.client import CodexClient
from app.services.codex.history import messages_to_history_items
from app.services.messages.default import message_service

log = structlog.get_logger(__name__)

_HISTORY_REPLAY_LIMIT = 10
_QUARANTINE_KEY_PREFIX = "codex:thread:quarantine:"


@dataclass(frozen=True, slots=True)
class SessionBootstrap:
    db_user_id: int
    db_chat_id: int
    stored_thread_id: str | None
    is_admin: bool


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
        """Atomic test-and-clear of `steer_pending`."""
        if not self.steer_pending:
            return False
        self.steer_pending = False
        return True


class ChatSessionStore[K, B](ABC):
    def __init__(self) -> None:
        self._sessions: dict[K, asyncio.Future[ChatSession]] = {}
        self._lock = asyncio.Lock()

    async def _get_or_open(self, key: K, bootstrap_arg: B) -> ChatSession:
        future, is_creator = await self._claim_slot(key)
        if is_creator:
            await self._fulfil_slot(future, key, bootstrap_arg)
        return await future

    async def get(self, key: K) -> ChatSession | None:
        async with self._lock:
            future = self._sessions.get(key)
        if future is None or not future.done():
            return None
        if future.exception() is not None:
            return None
        return future.result()

    async def reset(self, key: K) -> bool:
        """Drop in-memory session + clear stored Codex thread id."""
        async with self._lock:
            future = self._sessions.pop(key, None)
        if future is None:
            return False
        session = await self._safe_resolve(future)
        if session is None:
            return False
        await self._close_one(session)
        await self._clear_persisted_thread(session.db_chat_id)
        log.info("chat_session_reset", key=key)
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

    async def interrupt_all_turns(self) -> int:
        """Cleanly cancel any in-flight turns. Returns count of cancelled turns."""
        async with self._lock:
            futures = list(self._sessions.values())
        cancelled = 0
        for future in futures:
            session = await self._safe_resolve(future)
            if session is None or session.current_turn_task is None:
                continue
            if await self.cancel_session_turn(session):
                cancelled += 1
        return cancelled

    async def cancel_session_turn(self, session: ChatSession) -> bool:
        if not await cancel_session_turn(session):
            return False
        await self._on_turn_interrupted(session)
        return True

    @staticmethod
    async def seed_history_if_fresh_thread(session: ChatSession) -> None:
        """If next run opens a new thread, inject recent DB history first."""
        if session.client.current_thread_id is not None:
            return
        async with SessionLocal() as db:
            recent = await message_service.list_recent(
                db,
                session.db_chat_id,
                limit=_HISTORY_REPLAY_LIMIT,
            )
        if recent and recent[-1].role is MessageRole.USER:
            recent = recent[:-1]
        items = messages_to_history_items(recent)
        if not items:
            return
        await session.client.ensure_thread()
        await session.client.inject_history(items)

    async def _claim_slot(
        self,
        key: K,
    ) -> tuple[asyncio.Future[ChatSession], bool]:
        async with self._lock:
            existing = self._sessions.get(key)
            if existing is not None and not _failed_attempt(existing):
                return existing, False
            future: asyncio.Future[ChatSession] = asyncio.get_running_loop().create_future()
            self._sessions[key] = future
            return future, True

    async def _fulfil_slot(
        self,
        future: asyncio.Future[ChatSession],
        key: K,
        bootstrap_arg: B,
    ) -> None:
        try:
            session = await self._build_session(key, bootstrap_arg)
        except Exception as exc:
            async with self._lock:
                if self._sessions.get(key) is future:
                    self._sessions.pop(key)
            future.set_exception(exc)
            raise
        future.set_result(session)

    async def _build_session(self, key: K, bootstrap_arg: B) -> ChatSession:
        boot = await self._bootstrap(key, bootstrap_arg)
        initial = boot.stored_thread_id if settings.CODEX_THREAD_REUSE_ENABLED else None
        if initial is not None and await cache.get(f"{_QUARANTINE_KEY_PREFIX}{initial}"):
            log.warning("thread_quarantined_skipping_resume", thread_id=initial)
            initial = None
        client = await self._open_codex_client(boot.db_chat_id, initial, is_admin=boot.is_admin)
        log.info(
            "chat_session_opened",
            key=key,
            db_chat_id=boot.db_chat_id,
            db_user_id=boot.db_user_id,
            is_admin=boot.is_admin,
            rehydrated_thread=boot.stored_thread_id is not None,
        )
        return ChatSession(
            client=client,
            db_chat_id=boot.db_chat_id,
            db_user_id=boot.db_user_id,
        )

    @abstractmethod
    async def _bootstrap(self, key: K, bootstrap_arg: B) -> SessionBootstrap:
        """Resolve surface key into DB user/chat ids and stored thread id."""

    async def _on_turn_interrupted(self, session: ChatSession) -> None:  # noqa: B027
        """Optional hook: surface може записати TURN_INTERRUPTED у свій журнал."""

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
            log.warning("chat_session_resolve_failed", error=str(exc))
            return None

    @staticmethod
    async def _clear_persisted_thread(db_chat_id: int) -> None:
        async with SessionLocal() as db:
            await chat_service.set_codex_thread_id(db, db_chat_id, None)
            await db.commit()

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

        url = settings.CODEX_CLI_URL if is_admin else settings.CODEX_CLI_GUEST_URL
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


async def cancel_session_turn(
    session: ChatSession,
    on_interrupted: Callable[[ChatSession], Awaitable[None]] | None = None,
) -> bool:
    """Best-effort cancel current turn. Returns True if anything was cancelled."""
    task = session.current_turn_task
    if task is None:
        return False
    with contextlib.suppress(Exception):
        await session.client.interrupt()
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError, Exception):
        await task
    if session.current_turn_task is task:
        session.current_turn_task = None
    if on_interrupted is not None:
        await on_interrupted(session)
    return True


def quarantine_key(thread_id: str) -> str:
    return f"{_QUARANTINE_KEY_PREFIX}{thread_id}"


def _failed_attempt(future: asyncio.Future[Any]) -> bool:
    """A done future with exception stays in the map only until next claim."""
    return future.done() and future.exception() is not None
