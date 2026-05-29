"""DTOs / enums for chat-activity pub/sub."""

from enum import StrEnum


class ChatActivityKind(StrEnum):
    STARTED = "started"
    ENDED = "ended"
