"""Codex plan usage via app-server `account/rateLimits/read` RPC.

Fetched on-demand from the sidecar — no filesystem coupling.
"""

from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

from app.services.codex.client import CodexClient


class UsageWindow(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    used_percent: float = Field(description="Скільки квоти вже витрачено у цьому вікні (0-100).")
    window_minutes: int = Field(description="Тривалість rate-limit вікна у хвилинах.")
    resets_at: datetime | None = Field(
        default=None, description="Коли вікно ресетне; None якщо невідомо."
    )

    @property
    def left_percent(self) -> float:
        return max(0.0, 100.0 - self.used_percent)


class CodexUsage(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    updated_at: datetime | None = Field(
        default=None, description="Коли snapshot прийшов від sidecar."
    )
    plan_type: str | None = Field(
        default=None, description="`plan_type` з `account/rateLimits/read` (напр. `pro`)."
    )
    primary: UsageWindow | None = Field(
        default=None, description="Основне rate-limit вікно (зазвичай 5 hours)."
    )
    secondary: UsageWindow | None = Field(default=None, description="Додаткове вікно (weekly).")


class RateLimitWindowIn(BaseModel):
    """Inbound rate-limit window from the sidecar."""

    model_config = ConfigDict(extra="ignore", alias_generator=to_camel)

    used_percent: float | None = None
    window_duration_mins: int | None = None
    resets_at: float | None = None


class RateLimitSnapshot(BaseModel):
    """Boundary DTO for the `account/rateLimits/read` reply."""

    model_config = ConfigDict(extra="ignore", alias_generator=to_camel)

    plan_type: str | None = None
    primary: RateLimitWindowIn | None = None
    secondary: RateLimitWindowIn | None = None


class CodexUsageService:
    async def latest(self, client: CodexClient) -> CodexUsage | None:
        snapshot = await client.read_rate_limits()
        if snapshot is None:
            return None
        return parse_usage(RateLimitSnapshot.model_validate(snapshot))


def parse_usage(snapshot: RateLimitSnapshot) -> CodexUsage:
    return CodexUsage(
        updated_at=datetime.now(UTC),
        plan_type=snapshot.plan_type,
        primary=_to_window(snapshot.primary),
        secondary=_to_window(snapshot.secondary),
    )


def _to_window(window: RateLimitWindowIn | None) -> UsageWindow | None:
    if window is None or window.used_percent is None:
        return None
    resets_at = (
        datetime.fromtimestamp(window.resets_at, UTC) if window.resets_at is not None else None
    )
    return UsageWindow(
        used_percent=window.used_percent,
        window_minutes=window.window_duration_mins or 0,
        resets_at=resets_at,
    )
