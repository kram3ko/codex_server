"""Високорівневий клієнт до Codex CLI app-server.

Обгортає JSON-RPC handshake → thread/start → turn/start → стрім notifications,
перекладає Codex-сповіщення у наші типізовані ChatEvent'и. Не знає про БД,
HTTP/WS і тим паче про AISM. Один CodexClient = один WS до sidecar'а.
"""

import logging
from collections.abc import AsyncIterator
from typing import Any

from app.services.codex.events import (
    ChatEvent,
    DoneEvent,
    ErrorEvent,
    TokenEvent,
    ToolCallEvent,
    ToolResultEvent,
)
from app.services.codex.transport import AppServerClient, AppServerError, Notification

logger = logging.getLogger(__name__)

_CLIENT_INFO = {"name": "codex-server", "version": "0.1.0"}

# JSON-RPC method names — single source of truth для wire-протоколу.
_M_INITIALIZE = "initialize"
_M_INITIALIZED = "initialized"
_M_THREAD_START = "thread/start"
_M_TURN_START = "turn/start"
_M_TURN_INTERRUPT = "turn/interrupt"

_N_TURN_STARTED = "turn/started"
_N_TURN_COMPLETED = "turn/completed"
_N_AGENT_MSG_DELTA = "item/agentMessage/delta"
_N_ITEM_STARTED = "item/started"
_N_ITEM_COMPLETED = "item/completed"

# Codex CLI item types які НЕ є tool-call'ами (повідомлення моделі, плани, hooks).
_NON_TOOL_ITEM_TYPES = frozenset(
    {"agentMessage", "userMessage", "reasoning", "plan", "hook", "system"}
)


class CodexClient:
    """Один CodexClient = одна сесія з Codex CLI sidecar.

    Flow: connect() → ensure_thread() → run_turn(...) → close().
    Thread створюється лазі-стилем при першому run_turn'і й живе до close().
    """

    def __init__(
        self,
        url: str,
        cwd: str,
        approval_policy: str,
        sandbox: str,
        request_timeout: float = 60.0,
    ) -> None:
        self._url = url
        self._cwd = cwd
        self._approval_policy = approval_policy
        self._sandbox = sandbox
        self._transport = AppServerClient(url=url, request_timeout=request_timeout)
        self._initialized = False
        self._thread_id: str | None = None
        self._current_turn_id: str | None = None

    async def connect(self) -> None:
        await self._transport.connect()
        result = await self._transport.request(
            _M_INITIALIZE,
            {"clientInfo": _CLIENT_INFO, "capabilities": {}},
        )
        await self._transport.notify(_M_INITIALIZED, {})
        self._initialized = True
        logger.info(
            "codex_handshake_done user_agent=%s codex_home=%s",
            (result or {}).get("userAgent"),
            (result or {}).get("codexHome"),
        )

    async def ensure_thread(self) -> str:
        if self._thread_id is not None:
            return self._thread_id
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
        logger.info("codex_thread_opened thread_id=%s", thread_id)
        return thread_id

    async def run_turn(
        self,
        text: str,
        attachments: tuple[str, ...] = (),
    ) -> AsyncIterator[ChatEvent]:
        thread_id = await self.ensure_thread()
        input_payload: list[dict[str, Any]] = [{"type": "text", "text": text}]
        input_payload.extend({"type": "image", "url": url} for url in attachments)

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

        # Накопичення робимо тут (єдина точка відповідальності): _translate
        # отримує його як fallback для DoneEvent.final_text, а handler
        # використовує тільки ev.final_text без власного аккумулятора.
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
        """Best-effort cancel поточного turn'а."""
        turn_id = self._current_turn_id
        if not turn_id:
            return
        try:
            await self._transport.request(_M_TURN_INTERRUPT, {"turnId": turn_id})
        except AppServerError as exc:
            if exc.code == -32601:
                logger.info("codex_interrupt_unsupported turn_id=%s", turn_id)
            else:
                logger.warning("codex_interrupt_failed turn_id=%s code=%s", turn_id, exc.code)

    async def close(self) -> None:
        await self._transport.close()


def _extract_turn_id(result: Any) -> str | None:
    if not isinstance(result, dict):
        return None
    tid = result.get("turnId") or (result.get("turn") or {}).get("id")
    return tid if isinstance(tid, str) and tid else None


def _translate(note: Notification, accumulated: str) -> ChatEvent | None:
    method = note.method
    params = note.params

    if method == _N_TURN_STARTED:
        return None

    if method == _N_AGENT_MSG_DELTA:
        delta = params.get("delta") or params.get("text") or ""
        return TokenEvent(delta=delta) if delta else None

    if method == _N_ITEM_STARTED:
        item = params.get("item") or {}
        if item.get("type") in _NON_TOOL_ITEM_TYPES:
            return None
        # Codex CLI міняв назви полів між версіями — fallback ланцюг покриває
        # старі (tool, args) і нові (toolName, arguments) shapes.
        return ToolCallEvent(
            name=str(item.get("toolName") or item.get("tool") or item.get("type") or ""),
            args=dict(item.get("arguments") or item.get("args") or {}),
        )

    if method == _N_ITEM_COMPLETED:
        item = params.get("item") or {}
        if item.get("type") in _NON_TOOL_ITEM_TYPES:
            return None
        return ToolResultEvent(
            name=str(item.get("toolName") or item.get("tool") or item.get("type") or ""),
            result=str(item.get("output") or item.get("result") or ""),
            error=str(item["error"]) if item.get("error") else None,
        )

    if method == _N_TURN_COMPLETED:
        return DoneEvent(final_text=str(params.get("finalText") or accumulated))

    return None
