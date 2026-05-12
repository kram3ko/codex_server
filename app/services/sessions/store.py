"""Per-chat state holder для surface'ів що серіалізують турни (TG bot).

Codex sidecar за відкритим thread'ом більше не тримаємо тут — WS живе тільки
впродовж одного `run_turn` (див. `services/codex/runner.py`). ChatSession —
тонкий контейнер для речей що мають жити **між турнами**: per-chat lock,
посилання на running task (для /stop), steer-flag.

Web не використовує цей store: there's no inter-turn state worth keeping
in-process — все живе у Postgres / Redis, fresh CodexClient per turn.
"""

import asyncio
import contextlib
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

import structlog

from app.db.base import SessionLocal
from app.services.chats.default import chat_service

log = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class SessionBootstrap:
    db_user_id: int
    db_chat_id: int
    is_admin: bool


@dataclass(slots=True)
class ChatSession:
    db_chat_id: int
    db_user_id: int
    is_admin: bool
    turn_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    current_turn_task: asyncio.Task | None = None


class ChatSessionStore[K, B](ABC):
    """Per-surface in-process metadata cache.

    Не зберігає WS connection'и (вони per-turn у `services/codex/runner.py`).
    Тримає лише id маппінг + lock + running-task ref для interrupt button.
    """

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
        await self._clear_persisted_thread(session.db_chat_id)
        log.info("chat_session_reset", key=key)
        return True

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

    async def _claim_slot(self, key: K) -> tuple[asyncio.Future[ChatSession], bool]:
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
        log.info(
            "chat_session_opened",
            key=key,
            db_chat_id=boot.db_chat_id,
            db_user_id=boot.db_user_id,
            is_admin=boot.is_admin,
        )
        return ChatSession(
            db_chat_id=boot.db_chat_id,
            db_user_id=boot.db_user_id,
            is_admin=boot.is_admin,
        )

    @abstractmethod
    async def _bootstrap(self, key: K, bootstrap_arg: B) -> SessionBootstrap:
        """Resolve surface key into DB user/chat ids."""

    async def _on_turn_interrupted(self, session: ChatSession) -> None:  # noqa: B027
        """Optional hook: surface може записати TURN_INTERRUPTED у свій журнал."""

    @staticmethod
    async def _safe_resolve(future: asyncio.Future[ChatSession]) -> ChatSession | None:
        try:
            return await future
        except Exception as exc:  # noqa: BLE001 — future несе будь-яку failure від setter'а
            log.warning("chat_session_resolve_failed", error=str(exc))
            return None

    @staticmethod
    async def _clear_persisted_thread(db_chat_id: int) -> None:
        async with SessionLocal() as db:
            await chat_service.set_codex_thread_id(db, db_chat_id, None)
            await db.commit()


async def cancel_session_turn(session: ChatSession) -> bool:
    """Best-effort cancel — task.cancel() + await. CodexClient interrupt робить
    окремий per-turn runner (бо WS короткоживий)."""
    task = session.current_turn_task
    if task is None:
        return False
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError, Exception):
        await task
    if session.current_turn_task is task:
        session.current_turn_task = None
    return True


def _failed_attempt(future: asyncio.Future[Any]) -> bool:
    """A done future with exception stays in the map only until next claim."""
    return future.done() and future.exception() is not None
