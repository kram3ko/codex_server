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

_M_INITIALIZE = "initialize"
_M_INITIALIZED = "initialized"
_M_THREAD_START = "thread/start"
_M_THREAD_RESUME = "thread/resume"
_M_TURN_START = "turn/start"
_M_TURN_STEER = "turn/steer"
_M_TURN_INTERRUPT = "turn/interrupt"

_N_TURN_STARTED = "turn/started"
_N_TURN_COMPLETED = "turn/completed"
_N_AGENT_MSG_DELTA = "item/agentMessage/delta"
_N_ITEM_STARTED = "item/started"
_N_ITEM_COMPLETED = "item/completed"

# Codex CLI v2 ThreadItem types що НЕ є tool-call'ами (camelCase via serde).
_NON_TOOL_ITEM_TYPES = frozenset({
    "agentMessage",
    "userMessage",
    "hookPrompt",
    "plan",
    "reasoning",
    "commandExecution",
    "fileChange",
})

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
    ) -> None:
        self._url = url
        self._cwd = cwd
        self._approval_policy = approval_policy
        self._sandbox = sandbox
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
            await self._transport.request(_M_THREAD_RESUME, {"threadId": thread_id})
        except AppServerError as exc:
            if _is_thread_not_found(exc) or exc.code == -32601:
                return False
            raise
        log.info("codex_thread_resumed", thread_id=thread_id)
        return True

    async def _open_new_thread(self) -> str:
        result = await self._transport.request(
            _M_THREAD_START,
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
        thread_id = await self.ensure_thread()
        input_payload = self._build_input(text, attachments)

        try:
            result = await self._transport.request(
                _M_TURN_START,
                {"threadId": thread_id, "input": input_payload},
            )
        except AppServerError as exc:
            yield ErrorEvent(code="codex_error", detail=str(exc))
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
            await self._transport.request(_M_TURN_INTERRUPT, {"turnId": turn_id})
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
                _M_TURN_STEER,
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
            _M_INITIALIZE,
            {"clientInfo": _CLIENT_INFO, "capabilities": {}},
        )
        await self._transport.notify(_M_INITIALIZED, {})
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
    method = note.method
    params = note.params

    if method == _N_TURN_STARTED:
        return None

    if method == _N_AGENT_MSG_DELTA:
        delta = params.get("delta", "")
        return TokenEvent(delta=delta) if delta else None

    if method == _N_ITEM_STARTED:
        item = params.get("item") or {}
        if item.get("type") in _NON_TOOL_ITEM_TYPES:
            return None
        return ToolCallEvent(
            name=str(item.get("toolName", "")),
            args=dict(item.get("arguments") or {}),
        )

    if method == _N_ITEM_COMPLETED:
        item = params.get("item") or {}
        item_type = item.get("type")
        # Codex може видати повний agent-message одним item замість серії
        # `agentMessage/delta` — підхоплюємо як TokenEvent щоб накопичувач
        # `accumulated` у run_turn зловив його у final_text.
        if item_type == "agentMessage":
            text = item.get("text") or ""
            return TokenEvent(delta=text) if text else None
        if item_type in _NON_TOOL_ITEM_TYPES:
            return None
        if item_type == "dynamicToolCall":
            return _dynamic_tool_call_to_event(item)
        return ToolResultEvent(
            name=str(item.get("toolName", "")),
            result=str(item.get("output", "")),
            error=str(item["error"]) if item.get("error") else None,
        )

    if method == _N_TURN_COMPLETED:
        return DoneEvent(final_text=str(params.get("finalText") or accumulated))

    return None
