"""Turn-as-a-Job inner runner.

`execute_turn_inner` — pure async function викликана з TaskIQ task у
`app/services/turns/tasks.py`. Lifecycle: бере active+slot locks → відкриває
codex → стрім у per-turn Redis → heartbeat locks/`turns.heartbeat_at` →
`finalize_once` на exit. Всі шляхи (success/error/cancel/ownership_lost) →
CAS-finalize — exactly-once terminal.
"""

import asyncio

import structlog

from app.db.base import SessionLocal
from app.grpc_generated.codex.v1 import chat_pb2
from app.models import TurnStatus
from app.services import rate_limit
from app.services.codex.error_codes import CodexErrorCode
from app.services.codex.runner import open_codex_turn
from app.services.turns import locks
from app.services.turns.default import turn_service, turn_stream
from app.services.turns.locks import LockAcquireOutcome
from app.services.users.default import user_service

# Lazy-importable: app.rpc.chat re-exports ChatRPC через __init__, котрий тягне
# service.py і циклить tasks.py → runner.py. Тримаємо `error_event`/`stream_turn`
# у function-scope imports.

log = structlog.get_logger(__name__)

# Half of `_LOCK_TTL_S` (locks.py) — safe refresh window під event-loop drift.
_HEARTBEAT_INTERVAL_S = 10.0


async def heartbeat_loop(turn_id: int, chat_id: int, sidecar: str) -> None:
    """CAS-refresh False = ownership lost → loop exits; runner ловить далі."""
    del sidecar  # legacy signature compat — slot lock прибрано
    while True:
        try:
            await asyncio.sleep(_HEARTBEAT_INTERVAL_S)
        except asyncio.CancelledError:
            return
        async with SessionLocal() as db:
            owned = await turn_service.heartbeat(db, turn_id)
            await db.commit()
        if not owned:
            log.warning("turn_heartbeat_ownership_lost", turn_id=turn_id)
            return
        if not await locks.heartbeat_active(chat_id, turn_id):
            log.warning("turn_active_lock_lost", turn_id=turn_id)
            return


async def execute_turn_inner(
    turn_id: int,
    *,
    text: str,
    image_urls: tuple[str, ...],
    voice_reply: bool,
    client_id: str | None,
) -> None:
    """Idempotent: терминальний turn → no-op. Вся state з БД, кvargs з handler-а."""
    async with SessionLocal() as db:
        turn = await turn_service.get_by_id(db, turn_id)
        if turn is None:
            log.error("turn_runner_row_missing", turn_id=turn_id)
            return
        if turn.status in (
            TurnStatus.COMPLETED,
            TurnStatus.FAILED,
            TurnStatus.CANCELLED,
        ):
            # At-least-once redelivery hit на вже finalize-нутий turn.
            return
        rl_user = await user_service.get(db, turn.user_id)
    if rl_user is None:
        log.error("turn_runner_user_missing", turn_id=turn_id, user_id=turn.user_id)
        return

    chat_id = turn.chat_id
    sidecar = turn.sidecar or "admin"
    is_admin = sidecar == "admin"

    cancelled = False
    terminal_status: TurnStatus | None = None
    terminal_error: tuple[str | None, str | None] = (None, None)
    terminal_published = False
    redelivery_skip = False
    heartbeat_task: asyncio.Task[None] | None = None

    from app.rpc.chat.stream import stream_turn

    try:
        async with locks.hold_turn_locks(chat_id, sidecar, turn_id) as outcome:
            if outcome == LockAcquireOutcome.REDELIVERY:
                log.warning("turn_lock_contention_redelivery", turn_id=turn_id)
                redelivery_skip = True
                return
            if outcome != LockAcquireOutcome.ACQUIRED:
                terminal_status = TurnStatus.FAILED
                terminal_error = (
                    CodexErrorCode.TURN_BUSY,
                    f"lock unavailable: {outcome.value}",
                )
                return

            heartbeat_task = asyncio.create_task(
                heartbeat_loop(turn_id, chat_id, sidecar),
                name=f"turn-heartbeat:{turn_id}",
            )

            async with open_codex_turn(chat_id, is_admin=is_admin) as client:
                # `mark_running` тепер відбувається у `stream._on_started`
                # callback одразу на `turn/start` — раніше ніж перший yielded
                # event. Тут нічого не робимо у hot loop.
                async for event in stream_turn(
                    client,
                    text,
                    chat_id,
                    turn.user_id,
                    image_urls=image_urls,
                    voice_reply=voice_reply,
                    client_id=client_id,
                    turn_id=turn_id,
                ):
                    event.event_id = await turn_stream.publish(turn_id, event)
                    async with SessionLocal() as db:
                        await turn_service.update_last_event(db, turn_id, event.event_id)
                        await db.commit()
                    kind = event.WhichOneof("kind")
                    if kind in ("done", "error"):
                        terminal_published = True
                        terminal_status = (
                            TurnStatus.COMPLETED if kind == "done" else TurnStatus.FAILED
                        )
                        if kind == "error":
                            terminal_error = (
                                event.error.code or "codex_error",
                                event.error.detail,
                            )
    except asyncio.CancelledError:
        cancelled = True
        log.info("turn_runner_cancelled", turn_id=turn_id)
        terminal_status = TurnStatus.CANCELLED
        raise
    except Exception as exc:
        log.exception("turn_runner_crashed", turn_id=turn_id)
        terminal_status = TurnStatus.FAILED
        terminal_error = ("codex_error", f"background crashed: {exc}")
        from app.rpc.chat.mappers import error_event as _error_event

        try:
            crash = _error_event(CodexErrorCode.CODEX_ERROR, f"background crashed: {exc}")
            crash.event_id = await turn_stream.publish(turn_id, crash)
            terminal_published = True
        except Exception:
            log.exception("turn_runner_crash_publish_failed", turn_id=turn_id)
    finally:
        if heartbeat_task is not None:
            heartbeat_task.cancel()
            try:
                await heartbeat_task
            except asyncio.CancelledError:
                pass
            except Exception:
                log.exception("turn_heartbeat_task_failed", turn_id=turn_id)

        # Cancelled/redelivery skip-аємо: browser-cancel сам сигнал, owner finalize.
        if not terminal_published and not cancelled and not redelivery_skip:
            try:
                dropped = _build_terminal_event(terminal_status, terminal_error)
                dropped.event_id = await turn_stream.publish(turn_id, dropped)
            except Exception:
                log.exception("turn_safety_terminal_publish_failed", turn_id=turn_id)

        # Redelivery теж НЕ finalize-ить — owner це робить.
        if terminal_status is not None and not redelivery_skip:
            error_code, error_detail = terminal_error
            async with SessionLocal() as db:
                await turn_service.finalize_once(
                    db,
                    turn_id,
                    terminal_status,
                    error_code=error_code,
                    error_detail=error_detail,
                )
                await db.commit()

        # Redelivery не власник rate-limit / stream cleanup — пропускаємо.
        if not redelivery_skip:
            await turn_stream.cleanup(turn_id)
            await rate_limit.release_turn(rl_user)


def _build_terminal_event(
    status: TurnStatus | None,
    error: tuple[str | None, str | None],
) -> chat_pb2.ChatEvent:
    from app.rpc.chat.mappers import error_event

    if status is TurnStatus.COMPLETED:
        return chat_pb2.ChatEvent(done=chat_pb2.DoneEvent(final_text=""))
    code = error[0] or CodexErrorCode.STREAM_DROPPED
    detail = error[1] or "background turn ended without terminal event"
    return error_event(code, detail)
