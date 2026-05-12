"""Codex ChatEvent layer — public surface.

- types       — pydantic frame-models + JSON helpers
- translate   — Codex sidecar notification → ChatEvent
- idle        — async-stream idle-timeout wrapper
"""

from app.services.codex.events.idle import iterate_with_idle_timeout
from app.services.codex.events.translate import CodexItem, CodexNotif, translate_notification
from app.services.codex.events.types import (
    Attachment,
    AttachmentKind,
    ChatEvent,
    DoneEvent,
    ErrorEvent,
    TokenEvent,
    ToolCallEvent,
    ToolCallRecord,
    ToolResultEvent,
    bytes_to_event,
    event_to_bytes,
    event_to_frame,
    frame_to_event,
)

__all__ = [
    "Attachment",
    "AttachmentKind",
    "ChatEvent",
    "CodexItem",
    "CodexNotif",
    "DoneEvent",
    "ErrorEvent",
    "TokenEvent",
    "ToolCallEvent",
    "ToolCallRecord",
    "ToolResultEvent",
    "bytes_to_event",
    "event_to_bytes",
    "event_to_frame",
    "frame_to_event",
    "iterate_with_idle_timeout",
    "translate_notification",
]
