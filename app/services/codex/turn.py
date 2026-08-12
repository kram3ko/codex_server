import asyncio
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any
from urllib.parse import urlparse

import structlog

from app.config import settings
from app.services.codex.diagnostics import TurnDiagnostics
from app.services.codex.error_codes import CodexErrorCode
from app.services.codex.events import ChatEvent, CodexNotif, DoneEvent, ErrorEvent, TokenEvent
from app.services.codex.events import translate_notification as translate
from app.services.codex.idle import IdleDecision, decide_idle
from app.services.codex.shared import (
    Method,
    RateLimitsUpdateCallback,
    StaleTurnStreamError,
    is_thread_not_found,
    stale_sidecar_turn_error,
)
from app.services.codex.thread import CodexThreadSession
from app.services.codex.transport import AppServerClient, AppServerError, Notification

log = structlog.get_logger(__name__)


class CodexTurnSession:
    def __init__(
        self,
        *,
        transport: AppServerClient,
        threads: CodexThreadSession,
        reasoning_effort: str | None,
    ) -> None:
        self._transport = transport
        self._threads = threads
        self._reasoning_effort = reasoning_effort
        self._current_turn_id: str | None = None
        self._idle_s: float | None = None
        self._idle_deadline: float | None = None
        self._turn_started_at: float | None = None
        self._last_idle_probe_at: float | None = None
        self._turn_diagnostics: TurnDiagnostics | None = None

    @property
    def current_turn_id(self) -> str | None:
        return self._current_turn_id

    def diagnostics(self) -> dict[str, Any]:
        if self._turn_diagnostics is None:
            data: dict[str, Any] = {
                "thread_id": self._threads.current_thread_id,
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

    async def run_turn(
        self,
        text: str,
        attachments: tuple[str, ...] = (),
        *,
        on_started: Callable[[str, str], Awaitable[None]] | None = None,
        idle_s: float | None = None,
        on_idle: Callable[[], Awaitable[bool | None]] | None = None,
        on_rate_limits_update: RateLimitsUpdateCallback | None = None,
    ) -> AsyncIterator[ChatEvent]:
        input_payload = self._build_input(text, attachments)
        result = await self._begin_turn_with_retry(input_payload)
        if isinstance(result, ErrorEvent):
            yield result
            return

        self._current_turn_id = _extract_turn_id(result)
        self._turn_diagnostics = TurnDiagnostics(
            thread_id=self._threads.current_thread_id,
            turn_id=self._current_turn_id,
        )
        self._turn_started_at = time.monotonic()
        self._last_idle_probe_at = None
        try:
            thread_id = self._threads.current_thread_id
            if on_started is not None and thread_id is not None:
                await on_started(self._current_turn_id, thread_id)
            accumulated = ""

            async for note in self.current_turn_notifications(
                idle_s,
                on_idle,
                on_rate_limits_update,
            ):
                self._record_raw_note(note)
                event = translate(note, accumulated)
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
            self._turn_started_at = None
            self._last_idle_probe_at = None

    async def probe_turn_status(self, thread_id: str, codex_turn_id: str) -> str | None:
        thread_data = await self._threads.read_thread(thread_id, include_turns=True)
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

    async def interrupt(self, *, thread_id: str, turn_id: str) -> bool:
        try:
            await self._transport.request(
                Method.TURN_INTERRUPT,
                {"threadId": thread_id, "turnId": turn_id},
            )
        except AppServerError as exc:
            stale = stale_sidecar_turn_error(exc)
            if stale is not None:
                raise stale from exc
            if exc.code == -32601:
                log.info(
                    "codex_interrupt_unsupported",
                    thread_id=thread_id,
                    turn_id=turn_id,
                )
            else:
                log.warning(
                    "codex_interrupt_failed",
                    thread_id=thread_id,
                    turn_id=turn_id,
                    code=exc.code,
                )
            return False
        return True

    async def steer(
        self,
        text: str,
        *,
        turn_id: str | None = None,
        thread_id: str | None = None,
    ) -> bool:
        target_thread = thread_id or self._threads.current_thread_id
        target_turn = turn_id or self._current_turn_id
        if not target_thread or not target_turn:
            return False
        try:
            await self._transport.request(
                Method.TURN_STEER,
                {
                    "threadId": target_thread,
                    "input": [{"type": "text", "text": text}],
                    "expectedTurnId": target_turn,
                },
            )
        except AppServerError as exc:
            stale = stale_sidecar_turn_error(exc)
            if stale is not None:
                raise stale from exc
            log.warning("codex_steer_failed", turn_id=target_turn, code=exc.code, msg=str(exc))
            return False
        self.extend_idle_deadline()
        log.info("codex_steered", turn_id=target_turn, text_len=len(text))
        return True

    async def read_rate_limits(self) -> dict[str, Any] | None:
        try:
            result = await self._transport.request(Method.ACCOUNT_RATE_LIMITS_READ)
        except AppServerError as exc:
            if exc.code == -32601:
                return None
            raise
        snapshot = result.get("rateLimits")
        return snapshot if isinstance(snapshot, dict) else None

    async def current_turn_notifications(
        self,
        idle_s: float | None,
        on_idle: Callable[[], Awaitable[bool | None]] | None,
        on_rate_limits_update: RateLimitsUpdateCallback | None = None,
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
                if await self._handle_account_notification(note, on_rate_limits_update):
                    continue
                if self._should_skip_current_turn_note(note):
                    continue
                self.extend_idle_deadline()
                yield note
        finally:
            self._idle_s = None
            self._idle_deadline = None

    async def _begin_turn_with_retry(
        self,
        input_payload: list[dict[str, Any]],
    ) -> dict[str, Any] | ErrorEvent:
        thread_id = await self._threads.ensure_thread()
        try:
            return await self._transport.request(
                Method.TURN_START,
                self._build_turn_params(thread_id, input_payload),
            )
        except AppServerError as exc:
            if not is_thread_not_found(exc):
                return ErrorEvent(code=CodexErrorCode.CODEX_ERROR, detail=str(exc))
        log.info("codex_thread_stale_retrying", stale_thread_id=thread_id)
        await self._threads.reset_thread()
        thread_id = await self._threads.ensure_thread()
        try:
            return await self._transport.request(
                Method.TURN_START,
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
            if parsed.scheme in {"http", "https", "data"}:
                payload.append({"type": "image", "url": attachment})
            else:
                payload.append({"type": "localImage", "path": attachment})
        return payload

    async def _handle_account_notification(
        self,
        note: Notification,
        on_rate_limits_update: RateLimitsUpdateCallback | None,
    ) -> bool:
        if note.method == Method.ACCOUNT_RATE_LIMITS_UPDATED:
            rate_limits = note.params.get("rateLimits")
            if on_rate_limits_update is not None and isinstance(rate_limits, dict):
                await on_rate_limits_update(rate_limits)
            return True
        if note.method != Method.THREAD_TOKEN_USAGE_UPDATED:
            return False
        if on_rate_limits_update is not None:
            rate_limits = await self.read_rate_limits()
            if rate_limits is not None:
                await on_rate_limits_update(rate_limits)
        return True

    def _should_skip_current_turn_note(self, note: Notification) -> bool:
        if self._is_stale_turn_note(note):
            self._record_stale_raw_note(note)
            return True
        if self._is_session_noise_note(note):
            self._record_noise_raw_note(note)
            return True
        return False

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
        now = time.monotonic()
        decision = decide_idle(
            now=now,
            turn_started_at=self._turn_started_at,
            has_active_items=bool(
                self._turn_diagnostics and self._turn_diagnostics.has_active_items()
            ),
            last_idle_probe_at=self._last_idle_probe_at,
            hard_cap_s=settings.CODEX_TURN_HARD_TIMEOUT_S,
            probe_interval_s=settings.CODEX_IDLE_PROBE_INTERVAL_S,
        )
        if decision is IdleDecision.HARD_CAP_EXCEEDED:
            log.warning(
                "codex_turn_hard_cap_exceeded",
                elapsed_s=now - (self._turn_started_at or 0),
                cap_s=settings.CODEX_TURN_HARD_TIMEOUT_S,
            )
            return False
        if decision is IdleDecision.NEEDS_PROBE:
            if on_idle is None:
                return False
            self._last_idle_probe_at = now
            return bool(await on_idle())
        self.extend_idle_deadline()
        if self._turn_diagnostics is not None:
            log.debug(
                "codex_idle_extended_via_local_state",
                **self._turn_diagnostics.snapshot(self._transport),
            )
        return True

    def _is_stale_turn_note(self, note: Notification) -> bool:
        return (
            self._current_turn_id is not None
            and note.turn_id is not None
            and note.turn_id != self._current_turn_id
        )

    @staticmethod
    def _is_session_noise_note(note: Notification) -> bool:
        return (
            note.turn_id is None
            and note.method != CodexNotif.TURN_COMPLETED
            and not note.method.startswith("item/")
        )

    def _record_stale_raw_note(self, note: Notification) -> None:
        if self._turn_diagnostics is None:
            return
        self._turn_diagnostics.absorb_stale_raw(note)
        diagnostics = self._turn_diagnostics.snapshot(self._transport)
        log.warning("codex_stale_turn_notification_ignored", **diagnostics)
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
        prev_active = self._turn_diagnostics.selected_active_item()
        self._turn_diagnostics.absorb_raw(note)
        match note.method:
            case CodexNotif.ITEM_STARTED:
                log.debug(
                    "codex_item_started",
                    **self._turn_diagnostics.snapshot(self._transport),
                )
            case CodexNotif.ITEM_COMPLETED:
                log.debug(
                    "codex_item_completed",
                    **self._turn_diagnostics.snapshot(self._transport),
                )
            case CodexNotif.TURN_COMPLETED:
                log.debug(
                    "codex_turn_completed_raw",
                    **self._turn_diagnostics.snapshot(self._transport),
                )
            case _:
                if prev_active != self._turn_diagnostics.selected_active_item():
                    log.debug(
                        "codex_active_item_changed",
                        **self._turn_diagnostics.snapshot(self._transport),
                    )


def _extract_turn_id(result: dict[str, Any]) -> str:
    return result["turn"]["id"]


__all__ = ["CodexTurnSession"]
