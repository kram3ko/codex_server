"""Pipeline що проганяє Codex-стрім у protobuf ChatEvent для web RPC.

Інкапсулює: idle-timeout, error/cancel-paths, fan-out у event_bus,
персистенс final/partial assistant message + emit подій у журнал.
"""

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass
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


@dataclass
class _StreamState:
    collector: StreamCollector
    partial_msg_id: int | None = None
    assistant_attached: bool = False
    events_count: int = 0
    last_event_type: str = "none"


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

    async def _on_item_boundary(item_type: str) -> None:
        del item_type
        await _persist_boundary(
            state,
            persisted_chat_id,
            user_pk,
            client_id=client_id,
            turn_id=turn_id,
        )

    stream = client.run_turn(
        text,
        attachments=image_urls,
        on_started=_on_started,
        idle_s=settings.WEB_TURN_TIMEOUT_SECONDS,
        on_idle=_on_idle,
        on_item_boundary=_on_item_boundary,
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
                    break
                case TokenEvent(delta=delta) if coalesce_bytes > 0:
                    pending_text.append(delta)
                    pending_size += len(delta.encode("utf-8"))
                    if pending_size >= coalesce_bytes:
                        flushed = _flush_token_buffer()
                        if flushed is not None:
                            yield flushed
                case _:
                    flushed = _flush_token_buffer()
                    if flushed is not None:
                        yield flushed
                    yield chat_event_to_pb(ev)
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

    assistant_msg = await _persist_assistant_turn(
        persisted_chat_id,
        user_pk,
        state.collector.final_text,
        state.collector.tool_calls,
        state.collector.attachments,
        client_id=client_id,
        msg_id=state.partial_msg_id,
    )
    await _attach_assistant_to_turn(state, turn_id, assistant_msg.id)

    # TTS off the hot path — finalize turn for client first, attach audio коли
    # synth закінчиться. Codex flagged the prior blocking flow.
    if voice_reply and state.collector.final_text.strip():
        asyncio.create_task(
            attach_tts_to_message(
                assistant_msg.id, state.collector.final_text, persisted_chat_id, user_pk
            )
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
        text = state.collector.final_text or state.collector.buffer
        message = None
        if _has_visible_content(state):
            message = await _persist_assistant_turn(
                persisted_chat_id,
                user_pk,
                text,
                state.collector.tool_calls,
                state.collector.attachments,
                client_id=client_id,
                msg_id=state.partial_msg_id,
            )
            await _attach_assistant_to_turn(state, turn_id, message.id)
        done = chat_pb2.DoneEvent(
            chat_id=persisted_chat_id,
            final_text=final_text_for_done_frame(text, state.collector.buffer),
        )
        if message is not None:
            done.message.CopyFrom(message_to_pb(message))
        return chat_pb2.ChatEvent(done=done)

    await _persist_visible_partial(state, persisted_chat_id, user_pk, client_id=client_id)
    return error_event(f"codex_reported_{raw_status}", raw_status)


async def _persist_boundary(
    state: _StreamState,
    persisted_chat_id: int,
    user_pk: int,
    *,
    client_id: str | None,
    turn_id: int | None,
) -> None:
    if not _has_visible_content(state):
        return
    msg = await _persist_assistant_turn(
        persisted_chat_id,
        user_pk,
        state.collector.buffer,
        state.collector.tool_calls,
        [],
        partial=True,
        client_id=client_id,
        msg_id=state.partial_msg_id,
        emit_journal=False,
    )
    state.partial_msg_id = msg.id
    await _attach_assistant_to_turn(state, turn_id, msg.id)


async def _persist_visible_partial(
    state: _StreamState,
    persisted_chat_id: int,
    user_pk: int,
    *,
    client_id: str | None,
) -> None:
    if not _has_visible_content(state):
        return
    await _persist_assistant_turn(
        persisted_chat_id,
        user_pk,
        state.collector.buffer,
        state.collector.tool_calls,
        state.collector.attachments,
        partial=True,
        client_id=client_id,
        msg_id=state.partial_msg_id,
    )


def _has_visible_content(state: _StreamState) -> bool:
    return bool(
        state.collector.buffer
        or state.collector.tool_calls
        or state.collector.attachments
    )


async def _attach_assistant_to_turn(
    state: _StreamState,
    turn_id: int | None,
    message_id: int,
) -> None:
    if turn_id is None or state.assistant_attached:
        return
    async with SessionLocal() as db:
        await turn_service.attach_assistant_message(db, turn_id, message_id)
        await db.commit()
    state.assistant_attached = True


async def _persist_assistant_turn(
    persisted_chat_id: int,
    user_pk: int,
    text: str,
    tool_calls: list[ToolCallRecord],
    tool_attachments: list[Attachment],
    *,
    partial: bool = False,
    client_id: str | None = None,
    msg_id: int | None = None,
    emit_journal: bool = True,
) -> Message:
    """Insert (msg_id=None) or update assistant message + emit journal event.

    Update-mode use:ється для streaming-persist — на кожному item/completed
    boundary'і ми UPDATE'имо існуючий row замість INSERT'у. На terminal-вибір
    (done/cancel/timeout) знов update'имо з фінальним станом.
    `partial=True` → meta.partial=true для UI badge "interrupted/streaming".
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
        if client_id:
            meta["client_id"] = client_id
        if msg_id is None:
            assistant_msg = await message_service.append(
                db,
                persisted_chat_id,
                MessageRole.ASSISTANT,
                text,
                meta=meta or None,
            )
        else:
            await message_service.update_text(db, msg_id, text, meta=meta if meta else None)
            assistant_msg = await db.get(Message, msg_id)
            if assistant_msg is None:
                # msg_id має існувати — щойно update'нули. None означає race
                # з DELETE chat'у; кидаємо щоб caller обробив як stream_dropped.
                raise LookupError(f"message {msg_id} disappeared mid-turn")
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
