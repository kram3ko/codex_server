"""Pipeline що проганяє Codex-стрім у protobuf ChatEvent для web RPC.

Інкапсулює: idle-timeout, error/cancel-paths, fan-out у event_bus,
персистенс final/partial assistant message + emit подій у журнал.
"""

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import structlog

from app.config import settings
from app.db.base import SessionLocal
from app.grpc_generated.codex.v1 import chat_pb2
from app.models import EventKind, Message, MessageRole, TurnStatus
from app.rpc._mappers import message_to_pb
from app.rpc.chat.mappers import (
    chat_event_to_pb,
    error_event,
    final_text_for_done_frame,
)
from app.rpc.chat.tts import attach_tts_to_message
from app.services.bus.default import event_bus
from app.services.codex import codex_remote
from app.services.codex.client import CodexClient, StaleTurnStreamError
from app.services.codex.collector import StreamCollector
from app.services.codex.error_codes import CodexErrorCode
from app.services.codex.events import (
    Attachment,
    DoneEvent,
    ErrorEvent,
    TokenEvent,
    ToolCallRecord,
)
from app.services.codex.runner import quarantine_thread
from app.services.codex.sidecar import SidecarName
from app.services.codex_usage import poller as usage_poller
from app.services.events.default import event_service
from app.services.messages.default import message_service
from app.services.turns.default import turn_service
from app.services.turns.probe import (
    CodexTurnTerminal,
    interrupt_best_effort,
    probe_or_extend_idle,
)
from app.services.uploads.default import upload_service

log = structlog.get_logger(__name__)

# Strong refs до fire-and-forget background tasks (TTS attach після final
# yield). Без цього GC може зібрати task посеред synth.
_BG_TASKS: set[asyncio.Task[None]] = set()


@dataclass
class _StreamState:
    """Steer-boundary segmentation state. Partial rows — transient live
    snapshots (тільки для streaming chronology); на successful done
    видаляються, лишається single final assistant row як source of truth.
    На error/cancel/timeout — partial-rows зберігаються як "що встигло
    прийти до обриву" (`persist_visible_partial`).
    """

    collector: StreamCollector
    events_count: int = 0
    last_event_type: str = "none"
    segment_buffer_offset: int = 0
    segment_tool_count: int = 0
    seen_steer_count: int = 0
    partial_msg_ids: list[int] = field(default_factory=list)

    def cut_segment(self) -> None:
        self.segment_buffer_offset = len(self.collector.buffer)
        self.segment_tool_count = len(self.collector.tool_calls)


async def stream_turn(
    client: CodexClient,
    text: str,
    persisted_chat_id: int,
    user_pk: int,
    *,
    image_urls: tuple[str, ...] = (),
    voice_reply: bool = False,
    client_id: str | None = None,
    turn_id: int | None = None,
    sidecar: SidecarName | None = None,
) -> AsyncIterator[chat_pb2.ChatEvent]:
    state = _StreamState(collector=StreamCollector())

    async def _on_started(codex_turn_id: str, thread_id: str) -> None:
        await _mark_started(turn_id, thread_id, codex_turn_id)

    async def _on_idle() -> bool:
        return await _handle_idle(
            client,
            persisted_chat_id=persisted_chat_id,
            turn_id=turn_id,
            events_count=state.events_count,
            last_event_type=state.last_event_type,
        )

    async def _check_steer_boundary() -> None:
        if turn_id is None:
            return
        cur = await codex_remote.peek_steer_count(turn_id)
        if cur <= state.seen_steer_count:
            return
        state.seen_steer_count = cur
        await _persist_segment(state, persisted_chat_id, user_pk)

    async def _on_usage_signal() -> None:
        # Sidecar шле `thread/tokenUsage/updated` → fan-out у pub/sub.
        if sidecar is not None:
            usage_poller.schedule_refresh(sidecar)

    stream = client.run_turn(
        text,
        attachments=image_urls,
        on_started=_on_started,
        idle_s=settings.WEB_TURN_TIMEOUT_SECONDS,
        on_idle=_on_idle,
        on_usage_signal=_on_usage_signal,
    )

    # Coalesce TokenEvent deltas щоб зменшити кількість HTTP/2 DATA фреймів і
    # тиск на upstream queue при concurrent гостях. Buffer flush'иться при:
    # (1) size >= settings.CHAT_TOKEN_COALESCE_BYTES; (2) будь-який не-Token event;
    # (3) DoneEvent/ErrorEvent. event_bus.publish + collector.absorb бачать
    # кожен event індивідуально — coalesce впливає тільки на client-facing yield.
    coalesce_bytes = settings.CHAT_TOKEN_COALESCE_BYTES
    pending_text: list[str] = []
    pending_size = 0

    def _flush_token_buffer() -> chat_pb2.ChatEvent | None:
        nonlocal pending_text, pending_size
        if not pending_text:
            return None
        merged = "".join(pending_text)
        pending_text = []
        pending_size = 0
        return chat_pb2.ChatEvent(token=chat_pb2.TokenEvent(delta=merged))

    try:
        async for ev in stream:
            state.events_count += 1
            state.last_event_type = type(ev).__name__
            await event_bus.publish(persisted_chat_id, ev)
            state.collector.absorb(ev)
            match ev:
                case ErrorEvent():
                    flushed = _flush_token_buffer()
                    if flushed is not None:
                        yield flushed
                    yield chat_event_to_pb(ev)
                    await _emit_event(
                        persisted_chat_id,
                        user_pk,
                        EventKind.TURN_FAILED,
                        {"code": ev.code, "detail": ev.detail},
                    )
                    return
                case DoneEvent():
                    flushed = _flush_token_buffer()
                    if flushed is not None:
                        yield flushed
                    # Якщо steer прилетів між останнім boundary-check і done
                    # (нема ані non-Token event, ані coalesce-flush) —
                    # pre-steer контент попав би у final одним blob. Flush
                    # тут гарантує pre/post steer rows розділені.
                    await _check_steer_boundary()
                    break
                case TokenEvent(delta=delta) if coalesce_bytes > 0:
                    pending_text.append(delta)
                    pending_size += len(delta.encode("utf-8"))
                    if pending_size >= coalesce_bytes:
                        flushed = _flush_token_buffer()
                        if flushed is not None:
                            yield flushed
                        # Steer-boundary check на flush — амортизовано рідко
                        # (раз на ~`coalesce_bytes` токенів), не на кожен token.
                        await _check_steer_boundary()
                case _:
                    flushed = _flush_token_buffer()
                    if flushed is not None:
                        yield flushed
                    yield chat_event_to_pb(ev)
                    # Non-Token події рідкі (tool calls, attachments) — check без
                    # амортизації, latency Redis GET ~0.5ms прийнятна.
                    await _check_steer_boundary()
    except CodexTurnTerminal as exc:
        event = await _event_from_probe_terminal(
            state,
            persisted_chat_id,
            user_pk,
            status=exc.status,
            raw_status=exc.detail or exc.status.value,
            client_id=client_id,
            turn_id=turn_id,
        )
        yield event
        return
    except StaleTurnStreamError as exc:
        await interrupt_best_effort(client, reason="web_stale_turn_stream")
        await quarantine_thread(client.current_thread_id)
        await _persist_visible_partial(state, persisted_chat_id, user_pk, client_id=client_id)
        detail = "stale-turn-notifications"
        yield error_event(CodexErrorCode.TURN_TIMEOUT, detail)
        await _emit_event(
            persisted_chat_id,
            user_pk,
            EventKind.THREAD_RESET,
            {"reason": "stale_turn_stream", "diagnostics": exc.diagnostics},
        )
        return
    except TimeoutError:
        # Idle-timeout — best-effort interrupt sidecar + quarantine thread,
        # інакше наступний run_turn пробує resume тої самої мертвої thread.
        diagnostics = client.turn_diagnostics()
        await interrupt_best_effort(client, reason="web_idle_timeout")
        await quarantine_thread(client.current_thread_id)
        await _persist_visible_partial(state, persisted_chat_id, user_pk, client_id=client_id)
        detail = f"idle>{settings.WEB_TURN_TIMEOUT_SECONDS}s"
        yield error_event(CodexErrorCode.TURN_TIMEOUT, detail)
        await _emit_event(
            persisted_chat_id,
            user_pk,
            EventKind.TURN_FAILED,
            {"code": CodexErrorCode.TURN_TIMEOUT, "detail": detail, "diagnostics": diagnostics},
        )
        return
    except asyncio.CancelledError:
        await interrupt_best_effort(client, reason="web_cancelled")
        await _persist_visible_partial(state, persisted_chat_id, user_pk, client_id=client_id)
        await _emit_event(
            persisted_chat_id,
            user_pk,
            EventKind.TURN_INTERRUPTED,
            {"source": "web", "partial_len": len(state.collector.buffer)},
        )
        raise
    except Exception as exc:
        log.exception("web_rpc_codex_run_turn_failed")
        yield error_event(CodexErrorCode.CODEX_ERROR, str(exc))
        await _emit_event(
            persisted_chat_id,
            user_pk,
            EventKind.TURN_FAILED,
            {"exc_type": type(exc).__name__},
        )
        return

    if not state.collector.done_seen:
        yield error_event(CodexErrorCode.STREAM_DROPPED, "Codex stream ended without completion")
        await _emit_event(
            persisted_chat_id,
            user_pk,
            EventKind.TURN_FAILED,
            {"reason": CodexErrorCode.STREAM_DROPPED},
        )
        return

    # Final-of-turn persist: single atomic transaction (INSERT final + attach
    # до turn + DELETE partials + TURN_COMPLETED emit). На crash будь-якого
    # кроку все rollback-иться — БД лишається consistent.
    final_text = state.collector.final_text or state.collector.buffer
    partials_count = len(state.partial_msg_ids)
    assistant_msg = await _finalize_turn_persist(
        persisted_chat_id,
        user_pk,
        final_text,
        list(state.collector.tool_calls),
        list(state.collector.attachments),
        client_id=client_id,
        turn_id=turn_id,
        partial_msg_ids=state.partial_msg_ids,
    )
    state.partial_msg_ids.clear()

    # TTS off the hot path — finalize turn for client first, attach audio коли
    # synth закінчиться. Тримаємо reference у module-level set щоб GC не вбив
    # orphan task посеред synth (RUF006).
    if voice_reply and state.collector.final_text.strip():
        tts_task = asyncio.create_task(
            attach_tts_to_message(
                assistant_msg.id, state.collector.final_text, persisted_chat_id, user_pk
            )
        )
        _BG_TASKS.add(tts_task)
        tts_task.add_done_callback(_BG_TASKS.discard)

    # Anomaly-only: empty final з partials або final коротший за buffer
    # (codex-reformat дав менше тексту ніж стримилось — раніше було data-loss).
    # Норма — silent; немає метричного pipeline у цьому setup-і щоб лити info.
    final_len = len(state.collector.final_text)
    buffer_len = len(state.collector.buffer)
    if (final_len == 0 and partials_count > 0) or buffer_len > final_len:
        log.warning(
            "web_done_anomaly",
            turn_id=turn_id,
            client_id=client_id,
            assistant_msg_id=assistant_msg.id,
            final_text_len=final_len,
            buffer_len=buffer_len,
            partials_deleted=partials_count,
        )
    yield chat_pb2.ChatEvent(
        done=chat_pb2.DoneEvent(
            chat_id=persisted_chat_id,
            final_text=final_text_for_done_frame(
                state.collector.final_text,
                state.collector.buffer,
            ),
            message=message_to_pb(assistant_msg),
        )
    )


async def _mark_started(
    turn_id: int | None,
    thread_id: str,
    codex_turn_id: str,
) -> None:
    if turn_id is None:
        return
    async with SessionLocal() as db:
        await turn_service.mark_running(db, turn_id, thread_id, codex_turn_id)
        await db.commit()


async def _handle_idle(
    client: CodexClient,
    *,
    persisted_chat_id: int,
    turn_id: int | None,
    events_count: int,
    last_event_type: str,
) -> bool:
    if await codex_remote.consume_steer(persisted_chat_id, client.current_turn_id):
        return client.extend_idle_deadline()
    if turn_id is not None and await probe_or_extend_idle(client, turn_id):
        return client.extend_idle_deadline()

    diagnostics = client.turn_diagnostics()
    log.error(
        "web_rpc_codex_idle_timeout",
        db_chat_id=persisted_chat_id,
        idle_timeout_s=settings.WEB_TURN_TIMEOUT_SECONDS,
        events_count=events_count,
        last_event_type=last_event_type,
        **diagnostics,
    )
    await interrupt_best_effort(client, reason="web_idle_timeout")
    return False


async def _event_from_probe_terminal(
    state: _StreamState,
    persisted_chat_id: int,
    user_pk: int,
    *,
    status: TurnStatus,
    raw_status: str,
    client_id: str | None,
    turn_id: int | None,
) -> chat_pb2.ChatEvent:
    if status == TurnStatus.COMPLETED:
        # Same архітектура що й normal-done path: INSERT final + bind + DELETE
        # partials. Skip persist на абсолютно пустий результат — нема що
        # commit-ити; partial-rows вже видалені у попередніх sweep-ах не
        # будуть, бо їх теж нема.
        full_text = state.collector.final_text or state.collector.buffer
        message = None
        has_content = bool(
            full_text
            or state.collector.tool_calls
            or state.collector.attachments
        )
        if has_content:
            message = await _finalize_turn_persist(
                persisted_chat_id,
                user_pk,
                full_text,
                list(state.collector.tool_calls),
                list(state.collector.attachments),
                client_id=client_id,
                turn_id=turn_id,
                partial_msg_ids=state.partial_msg_ids,
            )
            state.partial_msg_ids.clear()
        done = chat_pb2.DoneEvent(
            chat_id=persisted_chat_id,
            final_text=final_text_for_done_frame(full_text, state.collector.buffer),
        )
        if message is not None:
            done.message.CopyFrom(message_to_pb(message))
        return chat_pb2.ChatEvent(done=done)

    await _persist_visible_partial(state, persisted_chat_id, user_pk, client_id=client_id)
    return error_event(f"codex_reported_{raw_status}", raw_status)


async def _persist_visible_partial(
    state: _StreamState,
    persisted_chat_id: int,
    user_pk: int,
    *,
    client_id: str | None,
) -> None:
    """Error/cancel/timeout path: INSERT delta-segment з прапором partial=true.
    Partial-rows цього turn-у НЕ видаляються (Variant A: на successful done
    final overrides все, але на abort partial-rows є snapshot обриву —
    єдина наявна інформація). Якщо delta пуста — нема що зберігати."""
    text_delta, tools_delta = _segment_delta(state)
    if not (text_delta or tools_delta):
        return
    msg = await _persist_assistant_turn(
        persisted_chat_id,
        user_pk,
        text_delta,
        tools_delta,
        [],  # attachments persist-яться на final; abort → губимо їх (acceptable)
        partial=True,
        client_id=client_id,
    )
    state.partial_msg_ids.append(msg.id)
    state.cut_segment()


async def _persist_segment(
    state: _StreamState,
    persisted_chat_id: int,
    user_pk: int,
) -> None:
    """Steer-boundary partial snapshot: live chronology підказка під час
    streaming. На successful done усі partial-rows цього turn-у видаляються
    у `_finalize_turn_persist` (final assistant row є source of truth).
    Tools-only boundary (text_delta пустий) пропускаємо — інакше UI рендерить
    empty-text partial як false-positive 'Codex was thinking — interrupted'."""
    text_delta, tools_delta = _segment_delta(state)
    if not text_delta:
        return
    msg = await _persist_assistant_turn(
        persisted_chat_id,
        user_pk,
        text_delta,
        tools_delta,
        [],  # attachments persist-яться тільки на final у одному row
        partial=True,
        client_id=None,
        emit_journal=False,
    )
    state.partial_msg_ids.append(msg.id)
    state.cut_segment()


def _segment_delta(
    state: _StreamState,
) -> tuple[str, list[ToolCallRecord]]:
    """Delta text+tools з останнього `cut_segment` — лише для PARTIAL
    snapshot-ів. Final-path не використовує (там повний INSERT + DELETE
    partials атомарно у `_finalize_turn_persist`)."""
    text_delta = state.collector.buffer[state.segment_buffer_offset :]
    tools_delta = list(state.collector.tool_calls[state.segment_tool_count :])
    return text_delta, tools_delta


async def _finalize_turn_persist(
    persisted_chat_id: int,
    user_pk: int,
    text: str,
    tool_calls: list[ToolCallRecord],
    tool_attachments: list[Attachment],
    *,
    client_id: str | None,
    turn_id: int | None,
    partial_msg_ids: list[int],
) -> Message:
    """All-or-nothing final-of-turn persist: INSERT final assistant + attach
    до turn + DELETE transient partials + emit TURN_COMPLETED — все у одній
    транзакції. Якщо DELETE впаде, final/attach теж rollback (інакше БД
    залишилася б з final+partial duplicates або orphan attach)."""
    async with SessionLocal() as db:
        upload_ids = await upload_service.persist_attachments(
            db,
            chat_id=persisted_chat_id,
            user_id=user_pk,
            attachments=tool_attachments,
        )
        meta: dict[str, Any] = {}
        if tool_calls:
            meta["calls"] = tool_calls
        if upload_ids:
            meta["upload_ids"] = upload_ids
        if client_id:
            meta["client_id"] = client_id
        assistant_msg = await message_service.append(
            db,
            persisted_chat_id,
            MessageRole.ASSISTANT,
            text,
            meta=meta or None,
        )
        if turn_id is not None:
            await turn_service.attach_assistant_message(db, turn_id, assistant_msg.id)
        await message_service.delete_many(db, partial_msg_ids)
        await event_service.emit(
            db,
            EventKind.TURN_COMPLETED,
            chat_id=persisted_chat_id,
            user_id=user_pk,
            payload={
                "final_text_len": len(text),
                "tool_calls": len(tool_calls),
                "uploads": len(upload_ids),
            },
        )
        await db.commit()
        await db.refresh(assistant_msg)
        return assistant_msg


async def _persist_assistant_turn(
    persisted_chat_id: int,
    user_pk: int,
    text: str,
    tool_calls: list[ToolCallRecord],
    tool_attachments: list[Attachment],
    *,
    partial: bool = False,
    client_id: str | None = None,
    emit_journal: bool = True,
) -> Message:
    """INSERT новий assistant row + (опційно) emit journal event.

    `partial=True` → `meta.partial=true` → UI рендерить badge
    "interrupted/streaming". На final partial-flag НЕ ставимо.
    `client_id` — anchor для live streaming placeholder swap у Chat.svelte;
    пишеться тільки коли `not partial` (partial-rows ключуються по
    autoincrement id, інакше дубль ключа у Svelte keyed `{#each}` крашить
    компонент).
    `emit_journal=False` → пропустити TURN_COMPLETED/TURN_FAILED event у
    журналі (для partial-сегментів — final сам емітить термінальну подію).
    """
    async with SessionLocal() as db:
        upload_ids = await upload_service.persist_attachments(
            db,
            chat_id=persisted_chat_id,
            user_id=user_pk,
            attachments=tool_attachments,
        )
        meta: dict[str, Any] = {}
        if partial:
            meta["partial"] = True
        if tool_calls:
            meta["calls"] = tool_calls
        if upload_ids:
            meta["upload_ids"] = upload_ids
        if client_id and not partial:
            meta["client_id"] = client_id
        assistant_msg = await message_service.append(
            db,
            persisted_chat_id,
            MessageRole.ASSISTANT,
            text,
            meta=meta or None,
        )
        payload: dict[str, Any] = {
            "final_text_len": len(text),
            "tool_calls": len(tool_calls),
            "uploads": len(upload_ids),
        }
        if partial:
            payload["reason"] = CodexErrorCode.STREAM_DROPPED
        if emit_journal:
            await event_service.emit(
                db,
                EventKind.TURN_FAILED if partial else EventKind.TURN_COMPLETED,
                chat_id=persisted_chat_id,
                user_id=user_pk,
                payload=payload,
            )
        await db.commit()
        await db.refresh(assistant_msg)
        return assistant_msg


async def _emit_event(
    chat_id: int,
    user_pk: int,
    kind: EventKind,
    payload: dict[str, Any] | None = None,
) -> None:
    async with SessionLocal() as session:
        await event_service.emit(
            session,
            kind,
            chat_id=chat_id,
            user_id=user_pk,
            payload=payload,
        )
        await session.commit()
