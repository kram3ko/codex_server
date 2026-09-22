from enum import StrEnum


class IdleDecision(StrEnum):
    HARD_CAP_EXCEEDED = "hard_cap_exceeded"
    NEEDS_PROBE = "needs_probe"
    EXTEND_SILENTLY = "extend_silently"


def decide_idle(
    *,
    now: float,
    turn_started_at: float | None,
    has_active_items: bool,
    last_idle_probe_at: float | None,
    hard_cap_s: float,
    probe_interval_s: float,
) -> IdleDecision:
    if turn_started_at is not None and now - turn_started_at >= hard_cap_s:
        return IdleDecision.HARD_CAP_EXCEEDED
    if not has_active_items:
        return IdleDecision.NEEDS_PROBE
    if last_idle_probe_at is None:
        return IdleDecision.NEEDS_PROBE
    if now - last_idle_probe_at >= probe_interval_s:
        return IdleDecision.NEEDS_PROBE
    return IdleDecision.EXTEND_SILENTLY


__all__ = ["IdleDecision", "decide_idle"]
