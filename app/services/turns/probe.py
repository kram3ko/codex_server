"""Codex idle-status probe — спільний path для web + TG.

`CodexTurnTerminal` — typed terminal signal. Probe **не** finalize-ить
turn у БД (це робить runner) — лише raise-ить signal з mapped status.
Single owner для `finalize_once`: runner у своєму outer except.
"""

import structlog

from app.models import TurnStatus
from app.services.codex.client import CodexClient
from app.services.turns.status_map import CodexTurnStatus, map_codex_turn_status

log = structlog.get_logger(__name__)


class CodexTurnTerminal(Exception):
    """Terminal signal з потоку/probe. Runner reads `.status`/`.error_code`/`.detail`."""

    def __init__(
        self,
        status: TurnStatus,
        *,
        error_code: str | None = None,
        detail: str | None = None,
    ) -> None:
        self.status = status
        self.error_code = error_code
        self.detail = detail
        super().__init__(f"codex turn terminal: {status.value}")


async def interrupt_best_effort(client: CodexClient, *, reason: str) -> None:
    """Best-effort interrupt по live-state клієнта (active turn у тому ж WS).

    Catches network/timeout/etc — `client.interrupt` сам логує AppServerError.
    Якщо thread/turn ще не виставлені (виклик до того як sidecar відповів на
    `turn/start`) — нічого не шлемо."""
    thread_id = client.current_thread_id
    turn_id = client.current_turn_id
    if not thread_id or not turn_id:
        return
    try:
        await client.interrupt(thread_id=thread_id, turn_id=turn_id)
    except Exception as exc:
        log.warning(
            "codex_interrupt_best_effort_failed",
            reason=reason,
            thread_id=thread_id,
            turn_id=turn_id,
            error=str(exc),
            exc_type=type(exc).__name__,
        )


async def probe_or_extend_idle(client: CodexClient, turn_id: int) -> bool:
    """Status probe на idle:
    - `inProgress` → return True (caller extends idle deadline).
    - terminal mapped → raise `CodexTurnTerminal` (runner finalize-ить у БД).
    - unknown / probe-fail → return False (caller fall through до timeout).
    """
    if not (client.current_thread_id and client.current_turn_id):
        return False
    try:
        raw = await client.probe_turn_status(client.current_thread_id, client.current_turn_id)
    except Exception as exc:
        log.warning(
            "codex_probe_failed",
            turn_id=turn_id,
            error=str(exc),
            exc_type=type(exc).__name__,
        )
        return False
    if raw == CodexTurnStatus.IN_PROGRESS:
        return True
    mapped = map_codex_turn_status(raw)
    if mapped is None or mapped == TurnStatus.RUNNING:
        return False
    log.info(
        "codex_probe_terminal",
        turn_id=turn_id,
        codex_status=raw,
        mapped=mapped.value,
    )
    raise CodexTurnTerminal(
        mapped,
        error_code=None if mapped == TurnStatus.COMPLETED else f"codex_reported_{raw}",
        detail=raw,
    )
