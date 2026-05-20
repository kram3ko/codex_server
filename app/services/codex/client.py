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

import asyncio
import re
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from enum import StrEnum
from typing import Any
from urllib.parse import urlparse

import structlog

from app.config import settings
from app.services.codex.error_codes import CodexErrorCode
from app.services.codex.events import (
    ChatEvent,
    DoneEvent,
    ErrorEvent,
    TokenEvent,
)
from app.services.codex.events import (
    translate_notification as _translate,
)
from app.services.codex.transport import AppServerClient, AppServerError, Notification

log = structlog.get_logger(__name__)

_CLIENT_INFO = {"name": "codex-api", "version": "0.1.0"}


class StaleTurnStreamError(RuntimeError):
    """Raised when a resumed thread only emits events for an older turn."""

    def __init__(self, diagnostics: dict[str, Any]) -> None:
        super().__init__("stale turn notification storm")
        self.diagnostics = diagnostics


class StaleSidecarTurnError(RuntimeError):
    """Codex sidecar reports another turn as active for this thread."""

    def __init__(self, *, expected_turn_id: str, actual_turn_id: str) -> None:
        super().__init__(
            f"sidecar active turn mismatch: expected {expected_turn_id}, found {actual_turn_id}"
        )
        self.expected_turn_id = expected_turn_id
        self.actual_turn_id = actual_turn_id


class _Method(StrEnum):
    INITIALIZE = "initialize"
    INITIALIZED = "initialized"
    THREAD_START = "thread/start"
    THREAD_RESUME = "thread/resume"
    THREAD_READ = "thread/read"
    THREAD_INJECT_ITEMS = "thread/inject_items"
    TURN_START = "turn/start"
    TURN_STEER = "turn/steer"
    TURN_INTERRUPT = "turn/interrupt"
    ACCOUNT_RATE_LIMITS_READ = "account/rateLimits/read"


type ThreadChangeCallback = Callable[[str | None], Awaitable[None]]


type _ActiveItem = tuple[str | None, str | None, float | None]


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
        self.stale_raw_count = 0
        self.noise_raw_count = 0
        self.last_raw_method = "none"
        self.last_raw_turn_id: str | None = None
        self.last_stale_method = "none"
        self.last_stale_turn_id: str | None = None
        self.last_stale_at: float | None = None
        self.last_stale_item_type: str | None = None
        self.last_stale_tool: str | None = None
        self.last_noise_method = "none"
        self.last_noise_at: float | None = None
        self.last_item_type: str | None = None
        self.last_tool: str | None = None
        self.last_chat_event_type = "none"
        self.turn_completed_seen = False
        self._active_items: dict[str, _ActiveItem] = {}

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
            self._active_items[_item_key(item)] = (self.last_item_type, tool, now)
        elif note.method == "item/completed":
            self.completed_items += 1
            self._active_items.pop(_item_key(item), None)

    def absorb_stale_raw(self, note: Notification) -> None:
        now = time.monotonic()
        self.stale_raw_count += 1
        self.last_stale_at = now
        self.last_stale_method = note.method
        self.last_stale_turn_id = note.turn_id
        item = note.params.get("item")
        if isinstance(item, dict):
            item_type = item.get("type")
            self.last_stale_item_type = item_type if isinstance(item_type, str) else None
            self.last_stale_tool = _item_label(item)

    def absorb_noise_raw(self, note: Notification) -> None:
        self.noise_raw_count += 1
        self.last_noise_at = time.monotonic()
        self.last_noise_method = note.method

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
            "stale_raw_count": self.stale_raw_count,
            "noise_raw_count": self.noise_raw_count,
            "last_raw_method": self.last_raw_method,
            "last_raw_turn_id": self.last_raw_turn_id,
            "last_raw_age_s": _age(now, self.last_raw_at),
            "last_stale_method": self.last_stale_method,
            "last_stale_turn_id": self.last_stale_turn_id,
            "last_stale_age_s": _age(now, self.last_stale_at),
            "last_stale_item_type": self.last_stale_item_type,
            "last_stale_tool": self.last_stale_tool,
            "last_noise_method": self.last_noise_method,
            "last_noise_age_s": _age(now, self.last_noise_at),
            "last_item_type": self.last_item_type,
            "last_tool": self.last_tool,
            "last_chat_event_type": self.last_chat_event_type,
            "last_chat_event_age_s": _age(now, self.last_chat_event_at),
            "turn_completed_seen": self.turn_completed_seen,
        }
        active_item_type, active_tool, active_started_at = self._selected_active_item()
        data.update(
            {
                "active_items": len(self._active_items),
                "active_item_type": active_item_type,
                "active_tool": active_tool,
                "active_item_age_s": _age(now, active_started_at),
            }
        )
        data.update(transport.diagnostic_snapshot())
        return data

    def _selected_active_item(self) -> _ActiveItem:
        if not self._active_items:
            return (None, None, None)
        return max(self._active_items.values(), key=_active_item_rank)

    def has_active_items(self) -> bool:
        return bool(self._active_items)


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
        notification_queue_max: int | None = None,
        auth_token: str | None = None,
    ) -> None:
        self._url = url
        self._cwd = cwd
        self._approval_policy = approval_policy
        self._sandbox = sandbox
        self._reasoning_effort = reasoning_effort
        transport_kwargs: dict[str, Any] = {"url": url, "request_timeout": request_timeout}
        if notification_queue_max is not None:
            transport_kwargs["notification_queue_max"] = notification_queue_max
        if auth_token is not None:
            transport_kwargs["auth_token"] = auth_token
        self._transport = AppServerClient(**transport_kwargs)
        self._initialized = False
        self._thread_id: str | None = initial_thread_id
        self._thread_resumed_or_started = False
        self._current_turn_id: str | None = None
        self._idle_s: float | None = None
        self._idle_deadline: float | None = None
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

    def extend_idle_deadline(self) -> bool:
        if self._idle_s is None:
            return False
        self._idle_deadline = time.monotonic() + self._idle_s
        return True

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
        on_idle: Callable[[], Awaitable[bool | None]] | None = None,
        on_usage_signal: Callable[[], Awaitable[None]] | None = None,
    ) -> AsyncIterator[ChatEvent]:
        """Stream ChatEvent'и. `on_started(turn_id, thread_id)` fires як тільки
        sidecar повернув turn/start — caller робить `mark_running` у `turns`.

        `idle_s` ставить watchdog на notification-и поточного turn'а (не на
        ChatEvent). Чужі leftover-и з попереднього turn не мають скидати таймер
        і не мають доходити до translator.

        `on_usage_signal` fires на `thread/tokenUsage/updated` (thread-level,
        не turn-bound) — sidecar шле це навіть зі stale turn_id, тому НЕ
        фільтруємо за turn_id."""
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

            async for note in self._current_turn_notifications(idle_s, on_idle, on_usage_signal):
                self._record_raw_note(note)
                event = _translate(note, accumulated)
                if event is not None:
                    self._turn_diagnostics.absorb_chat_event(event)
                    if isinstance(event, TokenEvent):
                        accumulated += event.delta
                    yield event
                if event is not None and isinstance(event, DoneEvent):
                    return
        finally:
            self._idle_s = None
            self._idle_deadline = None
            # Не чистимо _current_turn_id — callers у власному finally читають його для CAS-drop.

    async def read_thread(
        self,
        thread_id: str | None = None,
        *,
        include_turns: bool = True,
    ) -> dict[str, Any] | None:
        """`thread/read` — отримати поточний стан thread-а у sidecar-і. Caller
        використовує для status probe-у на idle (codex реально вмирає або
        просто чекає MCP-tool у tool-call-у?). Невідомий метод (`-32601`)
        мапиться на `None` щоб caller fallback-нув на idle-timeout."""
        target = thread_id or self._thread_id
        if not target:
            return None
        try:
            result = await self._transport.request(
                _Method.THREAD_READ,
                {"threadId": target, "includeTurns": include_turns},
            )
        except AppServerError as exc:
            if exc.code == -32601:
                log.info("codex_thread_read_unsupported", thread_id=target)
                return None
            log.warning(
                "codex_thread_read_failed",
                thread_id=target,
                code=exc.code,
                msg=str(exc),
            )
            return None
        if not isinstance(result, dict):
            return None
        return result

    async def probe_turn_status(
        self,
        thread_id: str,
        codex_turn_id: str,
    ) -> str | None:
        """Витягує `turn.status` raw-літерал для конкретного turn-а через
        `thread/read(includeTurns=true)`. Returns `None` якщо thread/turn
        не знайдені або status відсутній — caller (idle probe) трактує
        як `mismatch/stale` і йде stale_sidecar path."""
        thread_data = await self.read_thread(thread_id, include_turns=True)
        if thread_data is None:
            return None
        turns = thread_data.get("turns")
        if not isinstance(turns, list):
            return None
        for turn in turns:
            if not isinstance(turn, dict):
                continue
            if turn.get("id") == codex_turn_id:
                status = turn.get("status")
                return status if isinstance(status, str) else None
        return None

    async def interrupt(self, turn_id: str | None = None) -> bool:
        """Send turn/interrupt. `turn_id` override → cross-worker interrupt
        через координати з `turns` table, не з in-memory state."""
        target = turn_id or self._current_turn_id
        if not target:
            return False
        try:
            await self._transport.request(_Method.TURN_INTERRUPT, {"turnId": target})
        except AppServerError as exc:
            stale = _stale_sidecar_turn_error(exc)
            if stale is not None:
                raise stale from exc
            if exc.code == -32601:
                log.info("codex_interrupt_unsupported", turn_id=target)
            else:
                log.warning("codex_interrupt_failed", turn_id=target, code=exc.code)
            return False
        return True

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
            stale = _stale_sidecar_turn_error(exc)
            if stale is not None:
                raise stale from exc
            log.warning("codex_steer_failed", turn_id=target_turn, code=exc.code, msg=str(exc))
            return False
        self.extend_idle_deadline()
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

    async def _current_turn_notifications(
        self,
        idle_s: float | None,
        on_idle: Callable[[], Awaitable[bool | None]] | None,
        on_usage_signal: Callable[[], Awaitable[None]] | None = None,
    ) -> AsyncIterator[Notification]:
        notes = self._transport.notifications()
        self._idle_s = idle_s
        self._idle_deadline = time.monotonic() + idle_s if idle_s is not None else None
        try:
            while True:
                try:
                    note = await self._next_notification(notes, on_idle)
                except StopAsyncIteration:
                    return
                # Перехоплюємо ДО stale-filter: thread/tokenUsage/updated
                # приходить зі stale turn_id, інакше signal губиться.
                if on_usage_signal is not None and note.method == "thread/tokenUsage/updated":
                    await on_usage_signal()
                if self._is_stale_turn_note(note):
                    self._record_stale_raw_note(note)
                    continue
                if self._is_session_noise_note(note):
                    self._record_noise_raw_note(note)
                    continue
                self.extend_idle_deadline()
                yield note
        finally:
            self._idle_s = None
            self._idle_deadline = None

    async def _next_notification(
        self,
        notes: AsyncIterator[Notification],
        on_idle: Callable[[], Awaitable[bool | None]] | None,
    ) -> Notification:
        while True:
            deadline = self._idle_deadline
            if deadline is None:
                return await anext(notes)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                if await self._handle_idle(on_idle):
                    continue
                raise TimeoutError
            try:
                async with asyncio.timeout(remaining):
                    return await anext(notes)
            except TimeoutError:
                if self._idle_deadline is not None and time.monotonic() < self._idle_deadline:
                    continue
                if await self._handle_idle(on_idle):
                    continue
                raise

    async def _handle_idle(
        self,
        on_idle: Callable[[], Awaitable[bool | None]] | None,
    ) -> bool:
        """Idle hit — decide whether to extend.

        Fast path: trust locally-tracked `_active_items` (sidecar already
        acknowledged item/started без matching item/completed) → extend без
        RPC. Queue policy drop-**oldest** зберігає терминальні події, тому
        item/completed для in-flight tool-у не може загубитись через overflow.
        Якщо sidecar помер — transport reader пушить sentinel у queue, наступний
        `anext` raise-ить StopAsyncIteration → loop exits природньо.

        Fallback: локально пусто → on_idle (зазвичай `probe_or_extend_idle`
        робить thread/read до sidecar)."""
        if self._turn_diagnostics is not None and self._turn_diagnostics.has_active_items():
            self.extend_idle_deadline()
            log.debug(
                "codex_idle_extended_via_local_state",
                **self._turn_diagnostics.snapshot(self._transport),
            )
            return True
        if on_idle is None:
            return False
        return bool(await on_idle())

    def _is_stale_turn_note(self, note: Notification) -> bool:
        return (
            self._current_turn_id is not None
            and note.turn_id is not None
            and note.turn_id != self._current_turn_id
        )

    def _is_session_noise_note(self, note: Notification) -> bool:
        return (
            note.turn_id is None
            and note.method != "turn/completed"
            and not note.method.startswith("item/")
        )

    def _record_stale_raw_note(self, note: Notification) -> None:
        if self._turn_diagnostics is None:
            return
        self._turn_diagnostics.absorb_stale_raw(note)
        diagnostics = self._turn_diagnostics.snapshot(self._transport)
        log.warning(
            "codex_stale_turn_notification_ignored",
            **diagnostics,
        )
        if (
            self._turn_diagnostics.raw_count == 0
            and self._turn_diagnostics.stale_raw_count >= settings.CODEX_STALE_STORM_THRESHOLD
        ):
            log.error("codex_stale_turn_storm", **diagnostics)
            raise StaleTurnStreamError(diagnostics)

    def _record_noise_raw_note(self, note: Notification) -> None:
        if self._turn_diagnostics is None:
            return
        self._turn_diagnostics.absorb_noise_raw(note)
        log.debug(
            "codex_session_notification_ignored",
            **self._turn_diagnostics.snapshot(self._transport),
        )

    def _record_raw_note(self, note: Notification) -> None:
        if self._turn_diagnostics is None:
            return
        prev_active = self._turn_diagnostics._selected_active_item()
        self._turn_diagnostics.absorb_raw(note)
        match note.method:
            case "item/started":
                log.debug(
                    "codex_item_started",
                    **self._turn_diagnostics.snapshot(self._transport),
                )
            case "item/completed":
                log.debug(
                    "codex_item_completed",
                    **self._turn_diagnostics.snapshot(self._transport),
                )
            case "turn/completed":
                log.debug(
                    "codex_turn_completed_raw",
                    **self._turn_diagnostics.snapshot(self._transport),
                )
            case _:
                if prev_active != self._turn_diagnostics._selected_active_item():
                    log.debug(
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
        except Exception as exc:
            log.warning(
                "codex_on_thread_change_failed",
                new=new_thread_id,
                error=str(exc),
            )


def _is_thread_not_found(exc: AppServerError) -> bool:
    """Sidecar restarted → stored thread_id stale, retry with fresh thread."""
    return exc.code == -32600 and "thread not found" in str(exc).lower()


_STALE_ACTIVE_TURN_RE = re.compile(
    r"expected active turn id `(?P<expected>[^`]+)` but found `(?P<actual>[^`]+)`"
)


def _stale_sidecar_turn_error(exc: AppServerError) -> StaleSidecarTurnError | None:
    if exc.code != -32600:
        return None
    match = _STALE_ACTIVE_TURN_RE.search(str(exc))
    if match is None:
        return None
    return StaleSidecarTurnError(
        expected_turn_id=match.group("expected"),
        actual_turn_id=match.group("actual"),
    )


def _extract_turn_id(result: dict[str, Any]) -> str:
    return result["turn"]["id"]


def _age(now: float, at: float | None) -> float | None:
    return None if at is None else round(now - at, 3)


def _item_key(item: dict[str, Any]) -> str:
    for key in ("id", "callId", "toolCallId"):
        value = item.get(key)
        if isinstance(value, str) and value:
            return value
    return f"{item.get('type')}:{_item_label(item)}"


def _active_item_rank(item: _ActiveItem) -> tuple[int, float]:
    item_type, tool, started_at = item
    return (_active_item_priority(item_type, tool), -(started_at or 0.0))


def _active_item_priority(item_type: str | None, tool: str | None) -> int:
    if item_type in {
        "commandExecution",
        "mcpToolCall",
        "dynamicToolCall",
        "webSearch",
        "imageGeneration",
        "fileChange",
    }:
        return 30
    if tool not in {None, "reasoning", "agentMessage", "userMessage"}:
        return 20
    if item_type == "agentMessage":
        return 10
    if item_type == "reasoning":
        return 5
    return 0


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
