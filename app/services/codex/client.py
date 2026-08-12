"""High-level Codex app-server client.

`CodexClient` is intentionally a thin facade: transport lifecycle stays here,
thread lifecycle lives in `thread.py`, and turn streaming/control lives in
`turn.py`.
"""

from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

import structlog

from app.services.codex.events import ChatEvent
from app.services.codex.shared import (
    Method,
    RateLimitsUpdateCallback,
    StaleSidecarTurnError,
    StaleTurnStreamError,
    ThreadChangeCallback,
)
from app.services.codex.thread import CodexThreadSession
from app.services.codex.transport import AppServerClient
from app.services.codex.turn import CodexTurnSession

log = structlog.get_logger(__name__)

_CLIENT_INFO = {"name": "codex-api", "version": "0.1.0"}


class CodexClient:
    """One CodexClient = one Codex sidecar conversation over one WS connection."""

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
        transport: AppServerClient | None = None,
    ) -> None:
        self._url = url
        if transport is None:
            transport_kwargs: dict[str, Any] = {"url": url, "request_timeout": request_timeout}
            if notification_queue_max is not None:
                transport_kwargs["notification_queue_max"] = notification_queue_max
            if auth_token is not None:
                transport_kwargs["auth_token"] = auth_token
            transport = AppServerClient(**transport_kwargs)
        self._transport = transport
        self._transport.register_request_handler(
            Method.MCP_ELICITATION_REQUEST,
            self._handle_mcp_elicitation_request,
        )
        self._initialized = False
        self._threads = CodexThreadSession(
            transport=self._transport,
            cwd=cwd,
            approval_policy=approval_policy,
            sandbox=sandbox,
            initial_thread_id=initial_thread_id,
            on_thread_change=on_thread_change,
        )
        self._turns = CodexTurnSession(
            transport=self._transport,
            threads=self._threads,
            reasoning_effort=reasoning_effort,
        )

    @property
    def current_thread_id(self) -> str | None:
        return self._threads.current_thread_id

    @property
    def current_turn_id(self) -> str | None:
        return self._turns.current_turn_id

    def turn_diagnostics(self) -> dict[str, Any]:
        return self._turns.diagnostics()

    def extend_idle_deadline(self) -> bool:
        return self._turns.extend_idle_deadline()

    async def connect(self) -> None:
        await self._transport.connect()
        await self._handshake()

    async def close(self) -> None:
        await self._transport.close()

    async def ensure_thread(self) -> str:
        return await self._threads.ensure_thread()

    async def read_thread(
        self,
        thread_id: str | None = None,
        *,
        include_turns: bool = True,
    ) -> dict[str, Any] | None:
        return await self._threads.read_thread(thread_id, include_turns=include_turns)

    async def inject_history(self, items: list[dict[str, Any]]) -> None:
        await self._threads.inject_history(items)

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
        async for event in self._turns.run_turn(
            text,
            attachments,
            on_started=on_started,
            idle_s=idle_s,
            on_idle=on_idle,
            on_rate_limits_update=on_rate_limits_update,
        ):
            yield event

    async def probe_turn_status(self, thread_id: str, codex_turn_id: str) -> str | None:
        return await self._turns.probe_turn_status(thread_id, codex_turn_id)

    async def interrupt(self, *, thread_id: str, turn_id: str) -> bool:
        return await self._turns.interrupt(thread_id=thread_id, turn_id=turn_id)

    async def steer(
        self,
        text: str,
        *,
        turn_id: str | None = None,
        thread_id: str | None = None,
    ) -> bool:
        return await self._turns.steer(text, turn_id=turn_id, thread_id=thread_id)

    async def read_rate_limits(self) -> dict[str, Any] | None:
        return await self._turns.read_rate_limits()

    async def _handle_mcp_elicitation_request(self, params: dict[str, Any]) -> dict[str, Any]:
        log.info(
            "codex_elicitation_auto_accept",
            server_name=params.get("serverName"),
            mode=params.get("mode"),
            message=params.get("message"),
        )
        return {"action": "accept", "content": {}, "_meta": None}

    async def _handshake(self) -> None:
        result = await self._transport.request(
            Method.INITIALIZE,
            {"clientInfo": _CLIENT_INFO, "capabilities": {}},
        )
        await self._transport.notify(Method.INITIALIZED, {})
        self._initialized = True
        log.info(
            "codex_handshake_done",
            user_agent=(result or {}).get("userAgent"),
            codex_home=(result or {}).get("codexHome"),
        )


__all__ = [
    "CodexClient",
    "StaleSidecarTurnError",
    "StaleTurnStreamError",
]
