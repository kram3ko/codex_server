"""StrEnum'и, що потрапляють у Postgres ENUM-типи.

Чому StrEnum: значення = ім'я → JSON/log/SQL читабельні без мапінгів.
Імена ENUM-типів задаються поряд (`name=`) — ALEMBIC сортує per-name.
"""

import enum


class ChatSource(enum.StrEnum):
    WEB = "WEB"
    TELEGRAM = "TELEGRAM"


class MessageRole(enum.StrEnum):
    USER = "USER"
    ASSISTANT = "ASSISTANT"
    TOOL = "TOOL"
    SYSTEM = "SYSTEM"


class EventKind(enum.StrEnum):
    """Журнал подій — таймлайн усього, що сталося в чаті/системі."""

    THREAD_OPENED = "THREAD_OPENED"
    THREAD_RESET = "THREAD_RESET"
    THREAD_LOST = "THREAD_LOST"  # transport reconnect → context invalidated
    TURN_STARTED = "TURN_STARTED"
    TURN_COMPLETED = "TURN_COMPLETED"
    TURN_INTERRUPTED = "TURN_INTERRUPTED"
    TURN_FAILED = "TURN_FAILED"
    ATTACHMENT_RECEIVED = "ATTACHMENT_RECEIVED"
    AUDIO_TRANSCRIBED = "AUDIO_TRANSCRIBED"
    ERROR = "ERROR"


# Postgres ENUM type names — single source of truth для Alembic міграцій.
ENUM_NAMES = {
    ChatSource: "chat_source",
    MessageRole: "message_role",
    EventKind: "event_kind",
}
