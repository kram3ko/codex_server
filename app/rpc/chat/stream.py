"""Pipeline що проганяє Codex-стрім у protobuf ChatEvent для web RPC.

Інкапсулює: idle-timeout, error/cancel-paths, fan-out у event_bus,
персистенс final/partial assistant message + emit подій у журнал.
"""

import asyncio
import contextlib
from collections.abc import AsyncIterator
from typing import Any

import structlog

from app.config import settings
from app.db.base import SessionLocal
from app.grpc_generated.codex.v1 import chat_pb2
from app.models import EventKind, MessageRole
from app.rpc.chat.mappers import (
    chat_event_to_pb,
    error_event,
    final_text_for_done_frame,
)
from app.rpc.chat.tts import attach_tts_to_message
from app.services.bus.default import event_bus
from app.services.codex import turn_registry
from app.services.codex.client import CodexClient
from app.services.codex.collector import StreamCollector
from app.services.codex.error_codes import CodexErrorCode
from app.services.codex.events import (
    Attachment,
    DoneEvent,
    ErrorEvent,
    ToolCallRecord,
)
from app.services.codex.runner import quarantine_thread
from app.services.events.default import event_service
from app.services.messages.default import message_service
from app.services.uploads.default import upload_service

log = structlog.get_logger(__name__)


async def stream_turn(
    client: CodexClient,
    text: str,
    persisted_chat_id: int,
    user_pk: int,
    *,
    image_urls: tuple[str, ...] = (),
    voice_reply: bool = False,
) -> AsyncIterator[chat_pb2.ChatEvent]:
    collector = StreamCollector()

    if client.current_thread_id:
        await turn_registry.register_pending(
            persisted_chat_id, client.current_thread_id, is_admin=True
        )

    async def _on_started(turn_id: str, thread_id: str) -> None:
        promoted = await turn_registry.promote(persisted_chat_id, turn_id)
        if not promoted:
            with contextlib.suppress(Exception):
                await client.interrupt(turn_id=turn_id)
            raise asyncio.CancelledError

    events_count = 0
    last_event_type = "none"

    async def _on_idle() -> None:
        log.error(
            "web_rpc_codex_idle_timeout",
            db_chat_id=persisted_chat_id,
            idle_timeout_s=settings.WEB_TURN_TIMEOUT_SECONDS,
            events_count=events_count,
            last_event_type=last_event_type,
        )
        with contextlib.suppress(Exception):
            await client.interrupt()

    stream = client.run_turn(
        text,
        attachments=image_urls,
        on_started=_on_started,
        idle_s=settings.WEB_TURN_TIMEOUT_SECONDS,
        on_idle=_on_idle,
    )

    try:
        async for ev in stream:
            events_count += 1
            last_event_type = type(ev).__name__
            await event_bus.publish(persisted_chat_id, ev)
            collector.absorb(ev)
            match ev:
                case ErrorEvent():
                    yield chat_event_to_pb(ev)
                    await _emit_event(
                        persisted_chat_id,
                        user_pk,
                        EventKind.TURN_FAILED,
                        {"code": ev.code, "detail": ev.detail},
                    )
                    return
                case DoneEvent():
                    break
                case _:
                    yield chat_event_to_pb(ev)
    except TimeoutError:
        # Idle-timeout — best-effort interrupt sidecar + quarantine thread,
        # інакше наступний run_turn пробує resume тої самої мертвої thread.
        with contextlib.suppress(Exception):
            await client.interrupt()
        await quarantine_thread(client.current_thread_id)
        detail = f"idle>{settings.WEB_TURN_TIMEOUT_SECONDS}s"
        yield error_event(CodexErrorCode.TURN_TIMEOUT, detail)
        await _emit_event(
            persisted_chat_id,
            user_pk,
            EventKind.TURN_FAILED,
            {"code": CodexErrorCode.TURN_TIMEOUT, "detail": detail},
        )
        return
    except asyncio.CancelledError:
        # Persist partial assistant text + tool calls so chat reload показує
        # interrupted bubble замість дірки; mirrors TG `handle_dropped_stream`.
        # Не yield-ити після CancelledError — async-gen транспорт уже закривається,
        # client бачить stream як cancelled, не як error frame.
        if collector.buffer or collector.tool_calls or collector.attachments:
            await _persist_assistant_turn(
                persisted_chat_id,
                user_pk,
                collector.buffer,
                collector.tool_calls,
                collector.attachments,
                partial=True,
            )
        await _emit_event(
            persisted_chat_id,
            user_pk,
            EventKind.TURN_INTERRUPTED,
            {"source": "web", "partial_len": len(collector.buffer)},
        )
        raise
    except Exception as exc:  # noqa: BLE001 — backstop для RPC stream'у, lift у ErrorEvent
        log.exception("web_rpc_codex_run_turn_failed")
        yield error_event(CodexErrorCode.CODEX_ERROR, str(exc))
        await _emit_event(
            persisted_chat_id,
            user_pk,
            EventKind.TURN_FAILED,
            {"exc_type": type(exc).__name__},
        )
        return

    if not collector.done_seen:
        yield error_event(CodexErrorCode.STREAM_DROPPED, "Codex stream ended without completion")
        await _emit_event(
            persisted_chat_id,
            user_pk,
            EventKind.TURN_FAILED,
            {"reason": CodexErrorCode.STREAM_DROPPED},
        )
        return

    assistant_msg_id = await _persist_assistant_turn(
        persisted_chat_id,
        user_pk,
        collector.final_text,
        collector.tool_calls,
        collector.attachments,
    )

    # TTS off the hot path — finalize turn for client first, attach audio коли
    # synth закінчиться. Codex flagged the prior blocking flow.
    if voice_reply and collector.final_text.strip():
        asyncio.create_task(
            attach_tts_to_message(
                assistant_msg_id, collector.final_text, persisted_chat_id, user_pk
            )
        )

    yield chat_pb2.ChatEvent(
        done=chat_pb2.DoneEvent(
            chat_id=persisted_chat_id,
            final_text=final_text_for_done_frame(collector.final_text, collector.buffer),
        )
    )


async def _persist_assistant_turn(
    persisted_chat_id: int,
    user_pk: int,
    text: str,
    tool_calls: list[ToolCallRecord],
    tool_attachments: list[Attachment],
    *,
    partial: bool = False,
) -> int:
    """Persist assistant message + emit journal event.

    `partial=True` коли турн обірваний (CancelledError) — meta тегається
    `partial: True` щоб UI відмалював badge; event — TURN_FAILED + reason.
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
        await event_service.emit(
            db,
            EventKind.TURN_FAILED if partial else EventKind.TURN_COMPLETED,
            chat_id=persisted_chat_id,
            user_id=user_pk,
            payload=payload,
        )
        await db.commit()
        await db.refresh(assistant_msg)
        return assistant_msg.id


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
