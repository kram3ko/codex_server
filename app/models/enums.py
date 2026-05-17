"""StrEnum'и, що потрапляють у Postgres ENUM-типи.

Чому StrEnum: значення = ім'я → JSON/log/SQL читабельні без мапінгів.
Імена ENUM-типів задаються поряд (`name=`) — ALEMBIC сортує per-name.
"""

import enum


class UserRole(enum.StrEnum):
    """Дві ролі: ADMIN — повний доступ (shell, file_change), USER — все інше
    (image_gen, web_search, mcp, view). Default — USER."""

    USER = "USER"
    ADMIN = "ADMIN"


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


class TurnStatus(enum.StrEnum):
    """Lifecycle одного turn-а як durable job.

    `STARTING` — row створений, ще не підхоплений worker-ом.
    `RUNNING` — worker отримав ownership, codex stream-ить події.
    `COMPLETED` — codex відповів `done`, assistant message persisted.
    `FAILED` — будь-яка помилка (codex_error, timeout, stale_sidecar, crash).
    `CANCELLED` — user/server interrupt до завершення.
    """

    STARTING = "STARTING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


# Termal статуси — single source of truth для finalize-once guard.
TURN_TERMINAL_STATUSES = frozenset(
    {TurnStatus.COMPLETED, TurnStatus.FAILED, TurnStatus.CANCELLED}
)


# Postgres ENUM type names — single source of truth для Alembic міграцій.
ENUM_NAMES = {
    UserRole: "user_role",
    ChatSource: "chat_source",
    MessageRole: "message_role",
    EventKind: "event_kind",
    TurnStatus: "turn_status",
}
