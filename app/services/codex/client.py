"""Високорівневий клієнт до Codex CLI app-server.

Обгортає JSON-RPC handshake → thread/start → turn/start → стрім notifications,
перекладає Codex-сповіщення у наші типізовані ChatEvent'и. Один CodexClient =
одна сесія з sidecar'ом.

Thread state живе in-memory на стороні sidecar. Ми тримаємо `_thread_id` теж
in-memory, плюс caller може передати `initial_thread_id` (з БД cache) для
token-економії та `on_thread_change` callback для запису нового id.

Reconnect-семантика:
- Transport (WS) lost mid-stream → `_ensure_alive()` піднімає WS і робить
  re-handshake. Sidecar може бути той самий або новий — ми ще не знаємо.
- Якщо thread_id з БД stale (sidecar встиг рестартувати) → перший
  `turn/start` повертає `-32600 thread not found` → інвалідейтимо +
  відкриваємо новий thread + retry один раз.
"""

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from enum import StrEnum
from typing import Any
from urllib.parse import urlparse

import structlog

from app.services.codex.events import (
    ChatEvent,
    DoneEvent,
    ErrorEvent,
    TokenEvent,
    ToolCallEvent,
    ToolResultEvent,
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


class _Notif(StrEnum):
    TURN_STARTED = "turn/started"
    TURN_COMPLETED = "turn/completed"
    AGENT_MSG_DELTA = "item/agentMessage/delta"
    ITEM_STARTED = "item/started"
    ITEM_COMPLETED = "item/completed"


class _Item(StrEnum):
    AGENT_MESSAGE = "agentMessage"
    USER_MESSAGE = "userMessage"
    HOOK_PROMPT = "hookPrompt"
    PLAN = "plan"
    REASONING = "reasoning"
    COMMAND_EXECUTION = "commandExecution"
    FILE_CHANGE = "fileChange"
    DYNAMIC_TOOL_CALL = "dynamicToolCall"


# Items that don't surface as progress events. agentMessage is hidden in the
# `started` branch but extracted as a TokenEvent in `completed` (the long
# fallback path when sidecar emits whole text instead of streaming deltas).
_HIDDEN_ITEMS: frozenset[str] = frozenset({
    _Item.USER_MESSAGE,
    _Item.HOOK_PROMPT,
    _Item.PLAN,
    _Item.REASONING,
})

# Built-in Codex ops we surface as «pseudo-tools» in the progress bar so
# the user sees activity instead of just "Thinking…".
_BUILTIN_OP_LABELS: dict[str, str] = {
    _Item.COMMAND_EXECUTION: "shell",
    _Item.FILE_CHANGE: "file_change",
}

type ThreadChangeCallback = Callable[[str | None], Awaitable[None]]


class CodexClient:
    """Один CodexClient = одна сесія з Codex CLI sidecar."""

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
        self._on_thread_change = on_thread_change
        self._alive_lock = asyncio.Lock()

    @property
    def current_thread_id(self) -> str | None:
        return self._thread_id

    async def connect(self) -> None:
        await self._transport.connect()
        await self._handshake()

    async def ensure_thread(self) -> str:
        """Ensure a usable thread_id is loaded into the sidecar.

        Three paths:
        1. We already opened a thread in this connection — reuse `_thread_id`.
        2. Caller passed a stored `initial_thread_id` — try `thread/resume`
           (sidecar reads it from disk). On success → reuse. On failure →
           treat as gone and open a fresh one.
        3. No id → `thread/start` opens a new thread.
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
        thread_id = ((result or {}).get("thread") or {}).get("id")
        if not isinstance(thread_id, str) or not thread_id:
            raise RuntimeError(
                f"thread/start response shape unexpected (Codex CLI version drift?): {result!r}"
            )
        self._thread_id = thread_id
        self._thread_resumed_or_started = True
        log.info("codex_thread_opened", thread_id=thread_id)
        await self._emit_thread_change(thread_id)
        return thread_id

    async def run_turn(
        self,
        text: str,
        attachments: tuple[str, ...] = (),
    ) -> AsyncIterator[ChatEvent]:
        await self._ensure_alive()
        input_payload = self._build_input(text, attachments)
        result = await self._begin_turn_with_retry(input_payload)
        if isinstance(result, ErrorEvent):
            yield result
            return

        self._current_turn_id = _extract_turn_id(result)
        accumulated = ""

        async for note in self._transport.notifications():
            event = _translate(note, accumulated)
            if event is None:
                continue
            if isinstance(event, TokenEvent):
                accumulated += event.delta
            yield event
            if isinstance(event, DoneEvent):
                self._current_turn_id = None
                return

    async def interrupt(self) -> None:
        turn_id = self._current_turn_id
        if not turn_id:
            return
        try:
            await self._transport.request(_Method.TURN_INTERRUPT, {"turnId": turn_id})
        except AppServerError as exc:
            if exc.code == -32601:
                log.info("codex_interrupt_unsupported", turn_id=turn_id)
            else:
                log.warning("codex_interrupt_failed", turn_id=turn_id, code=exc.code)

    async def steer(self, text: str) -> bool:
        """Append text to in-flight turn. Returns True if accepted."""
        thread_id = self._thread_id
        turn_id = self._current_turn_id
        if not thread_id or not turn_id:
            return False
        try:
            await self._transport.request(
                _Method.TURN_STEER,
                {
                    "threadId": thread_id,
                    "input": [{"type": "text", "text": text}],
                    "expectedTurnId": turn_id,
                },
            )
        except AppServerError as exc:
            log.warning("codex_steer_failed", turn_id=turn_id, code=exc.code, msg=str(exc))
            return False
        log.info("codex_steered", turn_id=turn_id, text_len=len(text))
        return True

    async def inject_history(self, items: list[dict[str, Any]]) -> None:
        """Append Responses-API items into the current thread's history.

        Used after opening a fresh thread to seed it with prior turns from
        our DB — gives Codex context without thread/resume (which is broken
        upstream, see openai/codex#21360).
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

    async def start_new_thread(self) -> None:
        prev = self._thread_id
        self._thread_id = None
        self._thread_resumed_or_started = False
        self._current_turn_id = None
        if prev is not None:
            log.info("codex_thread_reset", prev_thread_id=prev)
            await self._emit_thread_change(None)

    async def close(self) -> None:
        await self._transport.close()

    async def _begin_turn_with_retry(
        self,
        input_payload: list[dict[str, Any]],
    ) -> dict[str, Any] | ErrorEvent:
        """One optimistic turn/start; on stale-thread → drop cache + retry once."""
        thread_id = await self.ensure_thread()
        try:
            return await self._transport.request(
                _Method.TURN_START,
                self._build_turn_params(thread_id, input_payload),
            )
        except AppServerError as exc:
            if not _is_thread_not_found(exc):
                return ErrorEvent(code="codex_error", detail=str(exc))
        log.info("codex_thread_stale_retrying", stale_thread_id=thread_id)
        await self._invalidate_thread()
        thread_id = await self.ensure_thread()
        try:
            return await self._transport.request(
                _Method.TURN_START,
                self._build_turn_params(thread_id, input_payload),
            )
        except AppServerError as exc:
            return ErrorEvent(code="codex_error", detail=str(exc))

    async def _invalidate_thread(self) -> None:
        self._thread_id = None
        self._thread_resumed_or_started = False
        self._current_turn_id = None
        await self._emit_thread_change(None)

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
            if parsed.scheme in {"http", "https"}:
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

    async def _ensure_alive(self) -> None:
        """Reconnect + re-handshake if transport died mid-stream.

        Не інвалідейтимо thread_id тут — sidecar може бути той самий, в такому
        разі stored thread валідний. Якщо sidecar теж рестартувався — побачимо
        thread-not-found на наступному turn/start і обробимо retry'ем.
        """
        async with self._alive_lock:
            if self._transport.is_connected and self._initialized:
                return
            log.warning("codex_transport_lost", thread_id=self._thread_id)
            self._initialized = False
            self._thread_resumed_or_started = False
            await self._transport.connect()
            await self._handshake()

    async def _emit_thread_change(self, new_thread_id: str | None) -> None:
        if self._on_thread_change is None:
            return
        try:
            await self._on_thread_change(new_thread_id)
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "codex_on_thread_change_failed",
                new=new_thread_id,
                error=str(exc),
            )


def _builtin_op_args(item: dict[str, Any], item_type: str) -> dict[str, Any]:
    if item_type == _Item.COMMAND_EXECUTION:
        cmd = item.get("command") or item.get("commandLine") or ""
        if isinstance(cmd, list):
            cmd = " ".join(str(c) for c in cmd)
        return {"command": str(cmd)}
    if item_type == _Item.FILE_CHANGE:
        return {"changes": item.get("changes") or []}
    return {}


def _builtin_op_result(item: dict[str, Any], item_type: str) -> str:
    if item_type == _Item.COMMAND_EXECUTION:
        return str(item.get("output") or item.get("stdout") or "")
    return ""


def _dynamic_tool_call_to_event(item: dict[str, Any]) -> ToolResultEvent:
    """Codex CLI dynamicToolCall (image_generation тощо) → markdown result.

    `contentItems` may carry `inputText` (raw text) and `inputImage` (URL чи
    file path). Збираємо у markdown щоб PhotoChunk-парсер у `tg/output.py`
    підхопив автоматично.
    """
    parts: list[str] = []
    for ci in item.get("contentItems") or []:
        ci_type = ci.get("type")
        if ci_type == "inputText":
            text = (ci.get("text") or "").strip()
            if text:
                parts.append(text)
        elif ci_type == "inputImage":
            url = ci.get("imageUrl") or ""
            if url:
                tool = item.get("tool") or "image"
                parts.append(f"![{tool}]({url})")
    return ToolResultEvent(
        name=str(item.get("tool", "")),
        result="\n\n".join(parts),
        error=str(item["error"]) if item.get("error") else None,
    )


def _is_thread_not_found(exc: AppServerError) -> bool:
    """Sidecar restarted → stored thread_id stale, retry with fresh thread."""
    return exc.code == -32600 and "thread not found" in str(exc).lower()


def _extract_turn_id(result: Any) -> str | None:
    if not isinstance(result, dict):
        return None
    turn = result.get("turn")
    if not isinstance(turn, dict):
        return None
    tid = turn.get("id")
    return tid if isinstance(tid, str) and tid else None


def _translate(note: Notification, accumulated: str) -> ChatEvent | None:
    match note.method:
        case _Notif.TURN_STARTED:
            return None
        case _Notif.AGENT_MSG_DELTA:
            delta = note.params.get("delta", "")
            return TokenEvent(delta=delta) if delta else None
        case _Notif.ITEM_STARTED:
            return _on_item_started(note.params.get("item") or {})
        case _Notif.ITEM_COMPLETED:
            return _on_item_completed(note.params.get("item") or {}, accumulated)
        case _Notif.TURN_COMPLETED:
            return DoneEvent(final_text=str(note.params.get("finalText") or accumulated))
        case _:
            return None


def _on_item_started(item: dict[str, Any]) -> ChatEvent | None:
    item_type = item.get("type") or ""
    if item_type == _Item.AGENT_MESSAGE or item_type in _HIDDEN_ITEMS:
        return None
    if item_type in _BUILTIN_OP_LABELS:
        return ToolCallEvent(
            name=_BUILTIN_OP_LABELS[item_type],
            args=_builtin_op_args(item, item_type),
        )
    return ToolCallEvent(
        name=str(item.get("toolName", "")),
        args=dict(item.get("arguments") or {}),
    )


def _on_item_completed(item: dict[str, Any], accumulated: str) -> ChatEvent | None:
    item_type = item.get("type") or ""
    if item_type == _Item.AGENT_MESSAGE:
        return _agent_message_to_token(item, accumulated)
    if item_type in _HIDDEN_ITEMS:
        return None
    if item_type == _Item.DYNAMIC_TOOL_CALL:
        return _dynamic_tool_call_to_event(item)
    if item_type in _BUILTIN_OP_LABELS:
        return ToolResultEvent(
            name=_BUILTIN_OP_LABELS[item_type],
            result=_builtin_op_result(item, item_type),
            error=str(item["error"]) if item.get("error") else None,
        )
    return ToolResultEvent(
        name=str(item.get("toolName", "")),
        result=str(item.get("output", "")),
        error=str(item["error"]) if item.get("error") else None,
    )


def _agent_message_to_token(item: dict[str, Any], accumulated: str) -> ChatEvent | None:
    # Sidecar may emit the whole agent-message as one item instead of streaming
    # `agentMessage/delta`. Reconcile against `accumulated` to avoid double text.
    text = item.get("text") or ""
    if not text or accumulated.endswith(text):
        return None
    if text.startswith(accumulated):
        return TokenEvent(delta=text[len(accumulated):])
    return TokenEvent(delta=text)
