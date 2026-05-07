"""Codex CLI app-server клієнт.

- transport.AppServerClient — JSON-RPC 2.0 over WebSocket (низький рівень)
- client.CodexClient — handshake, threads, run_turn (високий рівень)
- events — типізовані ChatEvent для WS-handler'а
"""

from app.services.codex.client import CodexClient
from app.services.codex.events import (
    ChatEvent,
    DoneEvent,
    ErrorEvent,
    TokenEvent,
    ToolCallEvent,
    ToolResultEvent,
    event_to_frame,
)
from app.services.codex.transport import AppServerClient, AppServerError, Notification

__all__ = [
    "AppServerClient",
    "AppServerError",
    "ChatEvent",
    "CodexClient",
    "DoneEvent",
    "ErrorEvent",
    "Notification",
    "TokenEvent",
    "ToolCallEvent",
    "ToolResultEvent",
    "event_to_frame",
]
