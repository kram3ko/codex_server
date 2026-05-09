"""Codex plan usage via app-server `account/rateLimits/read` RPC.

Fetched on-demand from the sidecar — no filesystem coupling.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from app.services.codex.client import CodexClient


@dataclass(frozen=True, slots=True)
class UsageWindow:
    used_percent: float
    window_minutes: int
    resets_at: datetime | None

    @property
    def left_percent(self) -> float:
        return max(0.0, 100.0 - self.used_percent)


@dataclass(frozen=True, slots=True)
class CodexUsage:
    updated_at: datetime | None
    plan_type: str | None
    primary: UsageWindow | None
    secondary: UsageWindow | None


class CodexUsageService:
    async def latest(self, client: CodexClient) -> CodexUsage | None:
        snapshot = await client.read_rate_limits()
        if snapshot is None:
            return None
        return _parse_usage(snapshot)


def _parse_usage(payload: dict[str, Any]) -> CodexUsage:
    return CodexUsage(
        updated_at=datetime.now(UTC),
        plan_type=_optional_str(payload.get("planType")),
        primary=_parse_window(payload.get("primary")),
        secondary=_parse_window(payload.get("secondary")),
    )


def _parse_window(value: object) -> UsageWindow | None:
    if not isinstance(value, dict):
        return None
    used_percent = _optional_float(value.get("usedPercent"))
    if used_percent is None:
        return None
    return UsageWindow(
        used_percent=used_percent,
        window_minutes=_optional_int(value.get("windowDurationMins")) or 0,
        resets_at=_parse_unix(value.get("resetsAt")),
    )


def _parse_unix(value: object) -> datetime | None:
    if isinstance(value, int | float):
        return datetime.fromtimestamp(value, UTC)
    return None


def _optional_float(value: object) -> float | None:
    if isinstance(value, int | float):
        return float(value)
    return None


def _optional_int(value: object) -> int | None:
    if isinstance(value, int):
        return value
    return None


def _optional_str(value: object) -> str | None:
    return value if isinstance(value, str) and value else None
