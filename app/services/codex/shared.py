import re
from collections.abc import Awaitable, Callable
from enum import StrEnum
from typing import Any

from app.services.codex.transport import AppServerError


class StaleTurnStreamError(RuntimeError):
    """Raised when a resumed thread only emits events for an older turn."""

    def __init__(self, diagnostics: dict[str, Any]) -> None:
        super().__init__("stale turn notification storm")
        self.diagnostics = diagnostics


class StaleSidecarTurnError(RuntimeError):
    """Codex sidecar reports another turn as active for this thread."""

    def __init__(self, *, expected_turn_id: str, actual_turn_id: str) -> None:
        super().__init__(
            f"sidecar active turn mismatch: expected {expected_turn_id}, found {actual_turn_id}"
        )
        self.expected_turn_id = expected_turn_id
        self.actual_turn_id = actual_turn_id


class Method(StrEnum):
    INITIALIZE = "initialize"
    INITIALIZED = "initialized"
    THREAD_START = "thread/start"
    THREAD_RESUME = "thread/resume"
    THREAD_READ = "thread/read"
    THREAD_TURNS_LIST = "thread/turns/list"
    THREAD_INJECT_ITEMS = "thread/inject_items"
    TURN_START = "turn/start"
    TURN_STEER = "turn/steer"
    TURN_INTERRUPT = "turn/interrupt"
    MODEL_LIST = "model/list"
    ACCOUNT_RATE_LIMITS_READ = "account/rateLimits/read"
    ACCOUNT_RATE_LIMITS_UPDATED = "account/rateLimits/updated"
    THREAD_TOKEN_USAGE_UPDATED = "thread/tokenUsage/updated"
    MCP_ELICITATION_REQUEST = "mcpServer/elicitation/request"


type ThreadChangeCallback = Callable[[str | None], Awaitable[None]]
type RateLimitsUpdateCallback = Callable[[dict[str, Any]], Awaitable[None]]


_STALE_ACTIVE_TURN_RE = re.compile(
    r"expected active turn id `(?P<expected>[^`]+)` but found `(?P<actual>[^`]+)`"
)


def is_thread_not_found(exc: AppServerError) -> bool:
    return exc.code == -32600 and "thread not found" in str(exc).lower()


def stale_sidecar_turn_error(exc: AppServerError) -> StaleSidecarTurnError | None:
    if exc.code != -32600:
        return None
    match = _STALE_ACTIVE_TURN_RE.search(str(exc))
    if match is None:
        return None
    return StaleSidecarTurnError(
        expected_turn_id=match.group("expected"),
        actual_turn_id=match.group("actual"),
    )


__all__ = [
    "Method",
    "RateLimitsUpdateCallback",
    "StaleSidecarTurnError",
    "StaleTurnStreamError",
    "ThreadChangeCallback",
    "is_thread_not_found",
    "stale_sidecar_turn_error",
]
