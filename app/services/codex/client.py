"""Високорівневий клієнт до Codex CLI app-server.

Один `CodexClient` = одне з'єднання = **один turn** (per-turn lifecycle).
Caller робить: `connect()` → `run_turn(...)` → `close()`. Re-use інстансу
між турнами не передбачений: notifications-черга сидекара shared per WS,
leftover ноти попереднього turn'а заходили б у наступний (фіксили це раніше
через TurnRouter — тепер просто не тримаємо довгоживий клієнт).

Thread reuse через WS-кордон робить caller: передає `initial_thread_id` з
кешу (Postgres `chats.codex_thread_id`), client пробує `thread/resume`;
fail → відкриває новий thread + повідомляє через `on_thread_change`, щоб
кеш оновився.
"""

import time
from collections.abc import AsyncIterator, Awaitable, Callable
from enum import StrEnum
from typing import Any
from urllib.parse import urlparse

import structlog

from app.services.codex.error_codes import CodexErrorCode
from app.services.codex.events import (
    ChatEvent,
    DoneEvent,
    ErrorEvent,
    TokenEvent,
    iterate_with_idle_timeout,
)
from app.services.codex.events import (
    translate_notification as _translate,
)
from app.services.codex.transport import AppServerClient, AppServerError, Notification

log = structlog.get_logger(__name__)

_CLIENT_INFO = {"name": "codex-api", "version": "0.1.0"}


class _Method(StrEnum):
    INITIALIZE = "initialize"
    INITIALIZED = "initialized"
    THREAD_START = "thread/start"
    THREAD_RESUME = "thread/resume"
    THREAD_INJECT_ITEMS = "thread/inject_items"
    TURN_START = "turn/start"
    TURN_STEER = "turn/steer"
    TURN_INTERRUPT = "turn/interrupt"
    ACCOUNT_RATE_LIMITS_READ = "account/rateLimits/read"


type ThreadChangeCallback = Callable[[str | None], Awaitable[None]]


class _TurnDiagnostics:
    """Small in-memory state for explaining idle timeouts after the fact."""

    def __init__(self, *, thread_id: str | None, turn_id: str) -> None:
        now = time.monotonic()
        self.thread_id = thread_id
        self.turn_id = turn_id
        self.started_at = now
        self.last_raw_at: float | None = None
        self.last_chat_event_at: float | None = None
        self.raw_count = 0
        self.chat_event_count = 0
        self.completed_items = 0
        self.last_raw_method = "none"
        self.last_raw_turn_id: str | None = None
        self.last_item_type: str | None = None
        self.last_tool: str | None = None
        self.last_chat_event_type = "none"
        self.turn_completed_seen = False
        self.active_item_type: str | None = None
        self.active_tool: str | None = None
        self.active_item_started_at: float | None = None

    def absorb_raw(self, note: Notification) -> None:
        now = time.monotonic()
        self.raw_count += 1
        self.last_raw_at = now
        self.last_raw_method = note.method
        self.last_raw_turn_id = note.turn_id
        if note.method == "turn/completed":
            self.turn_completed_seen = True

        item = note.params.get("item")
        if not isinstance(item, dict):
            return
        item_type = item.get("type")
        tool = _item_label(item)
        self.last_item_type = item_type if isinstance(item_type, str) else None
        self.last_tool = tool
        if note.method == "item/started":
            self.active_item_type = self.last_item_type
            self.active_tool = tool
            self.active_item_started_at = now
        elif note.method == "item/completed":
            self.completed_items += 1
            if self.active_item_type == self.last_item_type and self.active_tool == tool:
                self.active_item_type = None
                self.active_tool = None
                self.active_item_started_at = None

    def absorb_chat_event(self, event: ChatEvent) -> None:
        self.chat_event_count += 1
        self.last_chat_event_at = time.monotonic()
        self.last_chat_event_type = type(event).__name__

    def snapshot(self, transport: AppServerClient) -> dict[str, Any]:
        now = time.monotonic()
        data: dict[str, Any] = {
            "thread_id": self.thread_id,
            "turn_id": self.turn_id,
            "turn_age_s": round(now - self.started_at, 3),
            "raw_count": self.raw_count,
            "chat_event_count": self.chat_event_count,
            "completed_items": self.completed_items,
            "last_raw_method": self.last_raw_method,
            "last_raw_turn_id": self.last_raw_turn_id,
            "last_raw_age_s": _age(now, self.last_raw_at),
            "last_item_type": self.last_item_type,
            "last_tool": self.last_tool,
            "last_chat_event_type": self.last_chat_event_type,
            "last_chat_event_age_s": _age(now, self.last_chat_event_at),
            "turn_completed_seen": self.turn_completed_seen,
            "active_item_type": self.active_item_type,
            "active_tool": self.active_tool,
            "active_item_age_s": _age(now, self.active_item_started_at),
        }
        data.update(transport.diagnostic_snapshot())
        return data


class CodexClient:
    """One CodexClient = one Codex sidecar conversation (one turn)."""

    def __init__(
        self,
        url: str,
        cwd: str,
        approval_policy: str,
        sandbox: str,
        request_timeout: float = 60.0,
        initial_thread_id: str | None = None,
        on_thread_change: ThreadChangeCallback | None = None,
        reasoning_effort: str | None = None,
    ) -> None:
        self._url = url
        self._cwd = cwd
        self._approval_policy = approval_policy
        self._sandbox = sandbox
        self._reasoning_effort = reasoning_effort
        self._transport = AppServerClient(url=url, request_timeout=request_timeout)
        self._initialized = False
        self._thread_id: str | None = initial_thread_id
        self._thread_resumed_or_started = False
        self._current_turn_id: str | None = None
        self._turn_diagnostics: _TurnDiagnostics | None = None
        self._on_thread_change = on_thread_change

    @property
    def current_thread_id(self) -> str | None:
        return self._thread_id

    @property
    def current_turn_id(self) -> str | None:
        return self._current_turn_id

    def turn_diagnostics(self) -> dict[str, Any]:
        if self._turn_diagnostics is None:
            data: dict[str, Any] = {
                "thread_id": self._thread_id,
                "turn_id": self._current_turn_id,
                "diagnostics": "not_started",
            }
            data.update(self._transport.diagnostic_snapshot())
            return data
        return self._turn_diagnostics.snapshot(self._transport)

    async def connect(self) -> None:
        await self._transport.connect()
        await self._handshake()

    async def ensure_thread(self) -> str:
        """Гарантує що sidecar має активний thread_id.

        1. Уже opened/resumed у цьому з'єднанні → reuse.
        2. Стартовий id був переданий → пробуємо `thread/resume`.
        3. Fail / немає id → `thread/start`.
        """
        if self._thread_id is not None and self._thread_resumed_or_started:
            return self._thread_id

        if self._thread_id is not None:
            stale = self._thread_id
            if await self._try_resume(stale):
                self._thread_resumed_or_started = True
                return stale
            log.info("codex_thread_resume_failed_opening_new", stale_thread_id=stale)
            self._thread_id = None
            await self._emit_thread_change(None)

        return await self._open_new_thread()

    async def _try_resume(self, thread_id: str) -> bool:
        try:
            await self._transport.request(_Method.THREAD_RESUME, {"threadId": thread_id})
        except AppServerError as exc:
            if _is_thread_not_found(exc) or exc.code == -32601:
                return False
            raise
        log.info("codex_thread_resumed", thread_id=thread_id)
        return True

    async def _open_new_thread(self) -> str:
        result = await self._transport.request(
            _Method.THREAD_START,
            {
                "cwd": self._cwd,
                "approvalPolicy": self._approval_policy,
                "sandbox": self._sandbox,
            },
        )
        thread_id: str = result["thread"]["id"]
        self._thread_id = thread_id
        self._thread_resumed_or_started = True
        log.info("codex_thread_opened", thread_id=thread_id)
        await self._emit_thread_change(thread_id)
        return thread_id

    async def run_turn(
        self,
        text: str,
        attachments: tuple[str, ...] = (),
        *,
        on_started: Callable[[str, str], Awaitable[None]] | None = None,
        idle_s: float | None = None,
        on_idle: Callable[[], Awaitable[None]] | None = None,
    ) -> AsyncIterator[ChatEvent]:
        """Stream ChatEvent'и. `on_started(turn_id, thread_id)` fires як тільки
        sidecar повернув turn/start — caller пише запис у Redis turn_registry.

        `idle_s` ставить watchdog НА СИРИЙ Notification-стрім (не на ChatEvent).
        Інакше довге `item/started{reasoning}` — translator повертає None →
        ChatEvent-стрім тихий → watchdog фалшиво fail'ить активний turn.
        """
        input_payload = self._build_input(text, attachments)
        result = await self._begin_turn_with_retry(input_payload)
        if isinstance(result, ErrorEvent):
            yield result
            return

        self._current_turn_id = _extract_turn_id(result)
        self._turn_diagnostics = _TurnDiagnostics(
            thread_id=self._thread_id,
            turn_id=self._current_turn_id,
        )
        try:
            if on_started is not None and self._thread_id is not None:
                await on_started(self._current_turn_id, self._thread_id)
            accumulated = ""

            notes = self._transport.notifications()
            if idle_s is not None:
                notes = iterate_with_idle_timeout(notes, idle_s, on_idle=on_idle)
            async for note in notes:
                self._record_raw_note(note)
                event = _translate(note, accumulated)
                if event is None:
                    continue
                self._turn_diagnostics.absorb_chat_event(event)
                if isinstance(event, TokenEvent):
                    accumulated += event.delta
                yield event
                if isinstance(event, DoneEvent):
                    return
        finally:
            self._current_turn_id = None

    async def interrupt(self, turn_id: str | None = None) -> None:
        """Send turn/interrupt. `turn_id` override дозволяє іншому воркеру
        перервати turn запущений на цьому ж sidecar'і — координати беруться
        з Redis turn_registry, не з in-memory state."""
        target = turn_id or self._current_turn_id
        if not target:
            return
        try:
            await self._transport.request(_Method.TURN_INTERRUPT, {"turnId": target})
        except AppServerError as exc:
            if exc.code == -32601:
                log.info("codex_interrupt_unsupported", turn_id=target)
            else:
                log.warning("codex_interrupt_failed", turn_id=target, code=exc.code)

    async def steer(
        self,
        text: str,
        *,
        turn_id: str | None = None,
        thread_id: str | None = None,
    ) -> bool:
        """Append text до running turn. `turn_id`/`thread_id` override —
        cross-worker steer через Redis-stored координати."""
        target_thread = thread_id or self._thread_id
        target_turn = turn_id or self._current_turn_id
        if not target_thread or not target_turn:
            return False
        try:
            await self._transport.request(
                _Method.TURN_STEER,
                {
                    "threadId": target_thread,
                    "input": [{"type": "text", "text": text}],
                    "expectedTurnId": target_turn,
                },
            )
        except AppServerError as exc:
            log.warning("codex_steer_failed", turn_id=target_turn, code=exc.code, msg=str(exc))
            return False
        log.info("codex_steered", turn_id=target_turn, text_len=len(text))
        return True

    async def inject_history(self, items: list[dict[str, Any]]) -> None:
        """Append Responses-API items до history поточного thread'а.

        Юзаємо після відкриття нового thread'у щоб засіяти його recent-history
        з нашої БД (`thread/resume` зламаний upstream, openai/codex#21360).
        """
        thread_id = self._thread_id
        if not thread_id or not items:
            return
        try:
            await self._transport.request(
                _Method.THREAD_INJECT_ITEMS,
                {"threadId": thread_id, "items": items},
            )
        except AppServerError as exc:
            log.warning("codex_inject_history_failed", code=exc.code, msg=str(exc))
            return
        log.info("codex_history_injected", thread_id=thread_id, items=len(items))

    async def read_rate_limits(self) -> dict[str, Any] | None:
        """Codex plan rate-limit snapshot. None коли sidecar не expose'ить."""
        try:
            result = await self._transport.request(_Method.ACCOUNT_RATE_LIMITS_READ)
        except AppServerError as exc:
            if exc.code == -32601:
                return None
            raise
        snapshot = result.get("rateLimits")
        return snapshot if isinstance(snapshot, dict) else None

    async def close(self) -> None:
        await self._transport.close()

    def _record_raw_note(self, note: Notification) -> None:
        if self._turn_diagnostics is None:
            return
        prev_active = (self._turn_diagnostics.active_item_type, self._turn_diagnostics.active_tool)
        self._turn_diagnostics.absorb_raw(note)
        match note.method:
            case "item/started":
                log.info(
                    "codex_item_started",
                    **self._turn_diagnostics.snapshot(self._transport),
                )
            case "item/completed":
                log.info(
                    "codex_item_completed",
                    **self._turn_diagnostics.snapshot(self._transport),
                )
            case "turn/completed":
                log.info(
                    "codex_turn_completed_raw",
                    **self._turn_diagnostics.snapshot(self._transport),
                )
            case _:
                if prev_active != (
                    self._turn_diagnostics.active_item_type,
                    self._turn_diagnostics.active_tool,
                ):
                    log.info(
                        "codex_active_item_changed",
                        **self._turn_diagnostics.snapshot(self._transport),
                    )

    async def _begin_turn_with_retry(
        self,
        input_payload: list[dict[str, Any]],
    ) -> dict[str, Any] | ErrorEvent:
        """One turn/start; stale-thread → invalidate + retry once."""
        thread_id = await self.ensure_thread()
        try:
            return await self._transport.request(
                _Method.TURN_START,
                self._build_turn_params(thread_id, input_payload),
            )
        except AppServerError as exc:
            if not _is_thread_not_found(exc):
                return ErrorEvent(code=CodexErrorCode.CODEX_ERROR, detail=str(exc))
        log.info("codex_thread_stale_retrying", stale_thread_id=thread_id)
        self._thread_id = None
        self._thread_resumed_or_started = False
        await self._emit_thread_change(None)
        thread_id = await self.ensure_thread()
        try:
            return await self._transport.request(
                _Method.TURN_START,
                self._build_turn_params(thread_id, input_payload),
            )
        except AppServerError as exc:
            return ErrorEvent(code=CodexErrorCode.CODEX_ERROR, detail=str(exc))

    def _build_turn_params(
        self,
        thread_id: str,
        input_payload: list[dict[str, Any]],
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"threadId": thread_id, "input": input_payload}
        if self._reasoning_effort:
            params["effort"] = self._reasoning_effort
        return params

    @staticmethod
    def _build_input(text: str, attachments: tuple[str, ...]) -> list[dict[str, Any]]:
        payload: list[dict[str, Any]] = [{"type": "text", "text": text}]
        for attachment in attachments:
            parsed = urlparse(attachment)
            # `data:` URIs carry bytes inline (OpenAI Vision accepts them).
            # http(s) — forward as-is; caller гарантує що URL досяжний з OpenAI.
            if parsed.scheme in {"http", "https", "data"}:
                payload.append({"type": "image", "url": attachment})
            else:
                payload.append({"type": "localImage", "path": attachment})
        return payload

    async def _handshake(self) -> None:
        result = await self._transport.request(
            _Method.INITIALIZE,
            {"clientInfo": _CLIENT_INFO, "capabilities": {}},
        )
        await self._transport.notify(_Method.INITIALIZED, {})
        self._initialized = True
        log.info(
            "codex_handshake_done",
            user_agent=(result or {}).get("userAgent"),
            codex_home=(result or {}).get("codexHome"),
        )

    async def _emit_thread_change(self, new_thread_id: str | None) -> None:
        if self._on_thread_change is None:
            return
        try:
            await self._on_thread_change(new_thread_id)
        except Exception as exc:  # noqa: BLE001 — user-supplied callback, isolate
            log.warning(
                "codex_on_thread_change_failed",
                new=new_thread_id,
                error=str(exc),
            )


def _is_thread_not_found(exc: AppServerError) -> bool:
    """Sidecar restarted → stored thread_id stale, retry with fresh thread."""
    return exc.code == -32600 and "thread not found" in str(exc).lower()


def _extract_turn_id(result: dict[str, Any]) -> str:
    return result["turn"]["id"]


def _age(now: float, at: float | None) -> float | None:
    return None if at is None else round(now - at, 3)


def _item_label(item: dict[str, Any]) -> str | None:
    for key in ("toolName", "tool", "name"):
        value = item.get(key)
        if isinstance(value, str) and value:
            return value
    item_type = item.get("type")
    if isinstance(item_type, str):
        match item_type:
            case "commandExecution":
                return "shell"
            case "fileChange":
                return "file_change"
            case "webSearch":
                return "web_search"
            case "imageGeneration":
                return "image_generation"
            case "imageView":
                return "image_view"
            case _:
                return item_type
    return None
