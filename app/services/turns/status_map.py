"""Codex sidecar ↔ local `TurnStatus` translation. Літерали верифікуються при
першому live probe-і; невідомі → `None` (caller fallback на reconcile-path)."""

from enum import StrEnum

from app.models.enums import TurnStatus

__all__ = [
    "CodexThreadStatus",
    "CodexTurnStatus",
    "map_codex_thread_status",
    "map_codex_turn_status",
]


class CodexTurnStatus(StrEnum):
    IN_PROGRESS = "inProgress"
    COMPLETED = "completed"
    INTERRUPTED = "interrupted"
    FAILED = "failed"


class CodexThreadStatus(StrEnum):
    ACTIVE = "active"
    IDLE = "idle"
    NOT_LOADED = "notLoaded"


_TURN_STATUS_MAP: dict[str, TurnStatus] = {
    CodexTurnStatus.IN_PROGRESS: TurnStatus.RUNNING,
    CodexTurnStatus.COMPLETED: TurnStatus.COMPLETED,
    CodexTurnStatus.INTERRUPTED: TurnStatus.CANCELLED,
    CodexTurnStatus.FAILED: TurnStatus.FAILED,
}


def map_codex_turn_status(raw: str | None) -> TurnStatus | None:
    if raw is None:
        return None
    return _TURN_STATUS_MAP.get(raw)


def map_codex_thread_status(raw: str | None) -> CodexThreadStatus | None:
    if raw is None:
        return None
    try:
        return CodexThreadStatus(raw)
    except ValueError:
        return None
