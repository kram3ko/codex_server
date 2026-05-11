"""ChatService — chat CRUD plus web turn streaming."""

import asyncio
import base64
import contextlib
import tempfile
from collections.abc import AsyncIterator
from io import BytesIO
from pathlib import Path
from typing import Any, override

import structlog
from connectrpc.code import Code
from connectrpc.errors import ConnectError
from connectrpc.request import RequestContext

from app.config import settings
from app.db.base import SessionLocal
from app.grpc_generated.codex.v1 import chat_pb2, common_pb2
from app.grpc_generated.codex.v1.chat_connect import ChatService as ChatProtocol
from app.models import Chat, EventKind, MessageRole
from app.rpc._auth import require_user
from app.rpc._mappers import chat_to_pb, to_struct, to_ts
from app.services.bus.default import event_bus
from app.services.chats.default import chat_service
from app.services.codex.events import (
    Attachment,
    DoneEvent,
    ErrorEvent,
    TokenEvent,
    ToolCallEvent,
    ToolCallRecord,
    ToolResultEvent,
    iterate_with_idle_timeout,
)
from app.services.codex_usage.default import codex_usage_service
from app.services.codex_usage.service import CodexUsage, UsageWindow
from app.services.events.default import event_service
from app.services.messages.default import message_service
from app.services.sessions.store import ChatSession
from app.services.sessions.web import web_sessions
from app.services.tts.base import SpeechSynthesisError
from app.services.tts.default import tts_service
from app.services.uploads.default import upload_service

_DEFAULT_LIMIT = 50
_MAX_LIMIT = 200

# Presigned S3/MinIO URLs leak access key + signature + path in error strings.
# Token-replace them before yielding to the client; raw text goes to structlog.
_PRESIGNED_MARKER = "X-Amz-Signature="
_REDACTED_URL = "[link expired or unavailable]"

log = structlog.get_logger(__name__)


class ChatRPC(ChatProtocol):
    @override
    async def list_chats(
        self,
        request: chat_pb2.ListChatsRequest,
        ctx: RequestContext,
    ) -> chat_pb2.ListChatsResponse:
        user = await require_user(ctx)
        limit = _resolve_limit(request.pagination)
        async with SessionLocal() as db:
            chats = await chat_service.list_for_user(db, user.id, limit=limit)
        return chat_pb2.ListChatsResponse(chats=[chat_to_pb(c) for c in chats])

    @override
    async def get_chat(
        self,
        request: chat_pb2.GetChatRequest,
        ctx: RequestContext,
    ) -> chat_pb2.Chat:
        user = await require_user(ctx)
        async with SessionLocal() as db:
            chat = await _load_chat_owned(db, request.chat_id, user.id)
        return chat_to_pb(chat)

    @override
    async def rename_chat(
        self,
        request: chat_pb2.RenameChatRequest,
        ctx: RequestContext,
    ) -> chat_pb2.Chat:
        user = await require_user(ctx)
        new_title: str | None = request.title.strip() or None
        async with SessionLocal() as db:
            chat = await _load_chat_owned(db, request.chat_id, user.id)
            chat.title = new_title
            await db.commit()
            await db.refresh(chat)
            return chat_to_pb(chat)

    @override
    async def delete_chat(
        self,
        request: chat_pb2.DeleteChatRequest,
        ctx: RequestContext,
    ) -> common_pb2.Empty:
        user = await require_user(ctx)
        async with SessionLocal() as db:
            chat = await _load_chat_owned(db, request.chat_id, user.id)
            await db.delete(chat)
            await db.commit()
        return common_pb2.Empty()

    @override
    async def run_turn(
        self,
        request: chat_pb2.RunTurnRequest,
        ctx: RequestContext,
    ) -> AsyncIterator[chat_pb2.ChatEvent]:
        user = await require_user(ctx)
        text = request.text.strip()
        if not text:
            yield _error_event("empty_text", "text is required")
            return

        session = await web_sessions.get_or_open(user.id)
        if request.HasField("chat_id") and request.chat_id != session.db_chat_id:
            raise ConnectError(Code.NOT_FOUND, f"chat {request.chat_id} not found")

        persisted_chat_id = session.db_chat_id
        user_pk = session.db_user_id
        upload_ids = list(request.upload_ids)
        data_urls, image_ids, audio_ids = await _resolve_uploads(upload_ids)
        voice_reply = bool(audio_ids)
        user_meta: dict[str, Any] | None = None
        if image_ids or audio_ids:
            user_meta = {}
            if image_ids:
                user_meta["upload_ids"] = image_ids
            if audio_ids:
                user_meta["audio_upload_ids"] = audio_ids
        async with SessionLocal() as db:
            await message_service.append(
                db, persisted_chat_id, MessageRole.USER, text, meta=user_meta
            )
            await event_service.emit(
                db,
                EventKind.TURN_STARTED,
                chat_id=persisted_chat_id,
                user_id=user_pk,
                payload={"text_len": len(text), "attachments": len(data_urls)},
            )
            await db.commit()

        async with session.turn_lock:
            await web_sessions.seed_history_if_fresh_thread(session)
            current = asyncio.current_task()
            session.current_turn_task = current
            try:
                async for event in _stream_turn(
                    session, text, persisted_chat_id, user_pk, data_urls, voice_reply
                ):
                    yield event
            finally:
                if session.current_turn_task is current:
                    session.current_turn_task = None

    @override
    async def interrupt_turn(
        self,
        request: chat_pb2.InterruptTurnRequest,
        ctx: RequestContext,
    ) -> chat_pb2.InterruptTurnResponse:
        user = await require_user(ctx)
        async with SessionLocal() as db:
            chat = await _load_chat_owned(db, request.chat_id, user.id)
        session = await web_sessions.get(user.id)
        if session is not None and session.db_chat_id == chat.id:
            await web_sessions.cancel_session_turn(session)
        return chat_pb2.InterruptTurnResponse()

    @override
    async def steer_turn(
        self,
        request: chat_pb2.SteerTurnRequest,
        ctx: RequestContext,
    ) -> chat_pb2.SteerTurnResponse:
        user = await require_user(ctx)
        text = request.text.strip()
        if not text:
            return chat_pb2.SteerTurnResponse(accepted=False)
        session = await web_sessions.get(user.id)
        if session is None or session.db_chat_id != request.chat_id:
            return chat_pb2.SteerTurnResponse(accepted=False)
        accepted = await session.client.steer(text)
        if accepted:
            async with SessionLocal() as db:
                await message_service.append(db, session.db_chat_id, MessageRole.USER, text)
                await db.commit()
        return chat_pb2.SteerTurnResponse(accepted=accepted)

    @override
    async def get_codex_usage(
        self,
        request: chat_pb2.GetCodexUsageRequest,
        ctx: RequestContext,
    ) -> chat_pb2.CodexUsage:
        user = await require_user(ctx)
        session = await web_sessions.get_or_open(user.id)
        usage = await codex_usage_service.latest(session.client)
        if usage is None:
            return chat_pb2.CodexUsage()
        return _codex_usage_to_pb(usage)


def _resolve_limit(p: common_pb2.Pagination) -> int:
    limit = p.limit if p.limit > 0 else _DEFAULT_LIMIT
    return min(limit, _MAX_LIMIT)


async def _load_chat_owned(db, chat_id: int, user_id: int) -> Chat:
    chat = await chat_service.get(db, chat_id)
    if chat is None or chat.user_id != user_id:
        raise ConnectError(Code.NOT_FOUND, f"chat {chat_id} not found")
    return chat


async def _resolve_uploads(
    upload_ids: list[int],
) -> tuple[tuple[str, ...], list[int], list[int]]:
    # Returns (data_urls_for_codex, image_ids, audio_ids). Codex receives
    # image data URIs inline (its `url` is forwarded straight to OpenAI, where
    # Docker-internal MinIO would be unreachable). Audio is excluded from the
    # codex input — the transcript already lives in `text` — but the id is
    # kept so the UI can render `<audio>` from history.
    if not upload_ids:
        return (), [], []
    urls: list[str] = []
    image_ids: list[int] = []
    audio_ids: list[int] = []
    async with SessionLocal() as db:
        for uid in upload_ids:
            upload = await upload_service.get(db, uid)
            if upload is None:
                raise ConnectError(Code.NOT_FOUND, f"upload {uid} not found")
            if upload.mime.startswith("audio/"):
                audio_ids.append(uid)
                continue
            image_ids.append(uid)
            buf = BytesIO()
            await upload_service.download_to_stream(upload, buf)
            b64 = base64.b64encode(buf.getvalue()).decode("ascii")
            urls.append(f"data:{upload.mime};base64,{b64}")
    return tuple(urls), image_ids, audio_ids


async def _stream_turn(
    session: ChatSession,
    text: str,
    persisted_chat_id: int,
    user_pk: int,
    attachments: tuple[str, ...] = (),
    voice_reply: bool = False,
) -> AsyncIterator[chat_pb2.ChatEvent]:
    final_text = ""
    streamed_text = ""
    tool_calls: list[ToolCallRecord] = []
    attachments: list[Attachment] = []
    done_seen = False
    stream = session.client.run_turn(text, attachments=attachments)
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
            await session.client.interrupt()

    try:
        async for ev in iterate_with_idle_timeout(
            stream,
            settings.WEB_TURN_TIMEOUT_SECONDS,
            on_idle=_on_idle,
        ):
            events_count += 1
            last_event_type = type(ev).__name__
            await event_bus.publish(persisted_chat_id, ev)
            match ev:
                case TokenEvent(delta=delta):
                    streamed_text += delta
                case ToolCallEvent(name=name, args=args):
                    tool_calls.append({"name": name, "args": args})
                case ToolResultEvent(attachments=tool_files):
                    attachments.extend(tool_files)
                case ErrorEvent(code=code, detail=detail):
                    yield _chat_event_to_pb(ev)
                    await _emit_event(
                        persisted_chat_id,
                        user_pk,
                        EventKind.TURN_FAILED,
                        {"code": code, "detail": detail},
                    )
                    return
                case DoneEvent(final_text=ft):
                    final_text = ft
                    done_seen = True
                    break
            yield _chat_event_to_pb(ev)
    except TimeoutError:
        detail = f"idle>{settings.WEB_TURN_TIMEOUT_SECONDS}s"
        yield _error_event("turn_timeout", detail)
        await _emit_event(
            persisted_chat_id,
            user_pk,
            EventKind.TURN_FAILED,
            {"code": "turn_timeout", "detail": detail},
        )
        return
    except asyncio.CancelledError:
        # Persist the interrupt event then propagate cancellation. Don't
        # `yield` after CancelledError — async-gen транспорт уже закривається,
        # client все одно бачить stream як cancelled, не як error frame.
        await _emit_event(
            persisted_chat_id,
            user_pk,
            EventKind.TURN_INTERRUPTED,
            {"source": "web"},
        )
        raise
    except Exception as exc:  # noqa: BLE001
        log.error("web_rpc_codex_run_turn_failed", exc_type=type(exc).__name__, error=str(exc))
        yield _error_event("codex_error", str(exc))
        await _emit_event(
            persisted_chat_id,
            user_pk,
            EventKind.TURN_FAILED,
            {"exc_type": type(exc).__name__},
        )
        return

    if not done_seen:
        yield _error_event("stream_dropped", "Codex stream ended without completion")
        await _emit_event(
            persisted_chat_id,
            user_pk,
            EventKind.TURN_FAILED,
            {"reason": "stream_dropped"},
        )
        return

    tts_upload_id: int | None = None
    if voice_reply and final_text.strip():
        tts_upload_id = await _synthesize_reply(final_text, persisted_chat_id)

    async with SessionLocal() as db:
        upload_ids = await upload_service.persist_attachments(
            db,
            persisted_chat_id,
            attachments,
        )
        meta: dict[str, Any] = {}
        if tool_calls:
            meta["calls"] = tool_calls
        if upload_ids:
            meta["upload_ids"] = upload_ids
        if tts_upload_id is not None:
            meta["audio_upload_ids"] = [tts_upload_id]
        await message_service.append(
            db,
            persisted_chat_id,
            MessageRole.ASSISTANT,
            final_text,
            meta=meta or None,
        )
        await event_service.emit(
            db,
            EventKind.TURN_COMPLETED,
            chat_id=persisted_chat_id,
            user_id=user_pk,
            payload={
                "final_text_len": len(final_text),
                "tool_calls": len(tool_calls),
                "uploads": len(upload_ids),
            },
        )
        await db.commit()

    yield chat_pb2.ChatEvent(
        done=chat_pb2.DoneEvent(
            chat_id=persisted_chat_id,
            final_text=_final_text_for_done_frame(final_text, streamed_text),
        )
    )


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


def _chat_event_to_pb(event) -> chat_pb2.ChatEvent:
    match event:
        case TokenEvent(delta=delta):
            return chat_pb2.ChatEvent(token=chat_pb2.TokenEvent(delta=delta))
        case ToolCallEvent(name=name, args=args):
            return chat_pb2.ChatEvent(
                tool_call=chat_pb2.ToolCallEvent(
                    name=name,
                    args=to_struct(args),
                )
            )
        case ToolResultEvent(name=name, text=text, attachments=attachments, error=error):
            pb = chat_pb2.ToolResultEvent(
                name=name,
                text=text,
                attachments=[_attachment_to_pb(a) for a in attachments],
            )
            if error is not None:
                pb.error = _redact_for_user(error, source="tool_result_error")
            return chat_pb2.ChatEvent(tool_result=pb)
        case ErrorEvent(code=code, detail=detail):
            redacted = _redact_for_user(detail, source="error_event") if detail else detail
            return _error_event(code, redacted)
        case DoneEvent(final_text=final_text):
            return chat_pb2.ChatEvent(done=chat_pb2.DoneEvent(final_text=final_text))
        case _:
            return _error_event("unknown_event", type(event).__name__)


async def _synthesize_reply(text: str, chat_id: int) -> int | None:
    """TTS → MinIO upload; returns upload_id, or None if TTS disabled/failed.

    TTS backend wants a Path → write to a tempfile, read bytes, upload, drop.
    Tempfile is the only on-disk hop in the otherwise streaming pipeline."""
    if not tts_service.enabled:
        return None
    with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp:
        tmp_path = Path(tmp.name)
    try:
        try:
            await tts_service.synthesize(text, tmp_path, audio_encoding="OGG_OPUS")
        except SpeechSynthesisError as exc:
            log.warning("web_tts_failed", chat_id=chat_id, error=str(exc)[:200])
            return None
        data = tmp_path.read_bytes()
    finally:
        with contextlib.suppress(FileNotFoundError):
            tmp_path.unlink()

    async def _one_chunk() -> AsyncIterator[bytes]:
        yield data

    async with SessionLocal() as db:
        upload = await upload_service.persist_chunks(
            db,
            chat_id=chat_id,
            filename="reply.ogg",
            mime="audio/ogg",
            chunks=_one_chunk(),
        )
        await db.commit()
        await db.refresh(upload)
    return upload.id


def _redact_for_user(text: str, *, source: str) -> str:
    """Strip presigned-URL noise. Log raw to keep debugging signal."""
    if _PRESIGNED_MARKER not in text:
        return text
    redacted = " ".join(_REDACTED_URL if _PRESIGNED_MARKER in tok else tok for tok in text.split())
    log.warning("user_facing_error_redacted", source=source, raw=text)
    return redacted


_GENERATED_PREFIX = "/home/codex/.codex/generated_images/"


def _attachment_to_pb(attachment: Attachment) -> chat_pb2.Attachment:
    source = attachment.source
    # Image generation file path → public web URL — рендериться під час
    # streamу без чекання S3 persist.
    if source.startswith(_GENERATED_PREFIX):
        source = "/generated/" + source[len(_GENERATED_PREFIX) :]
    return chat_pb2.Attachment(
        kind=attachment.kind.value,
        source=source,
        caption=attachment.caption,
    )


def _error_event(code: str, detail: str | None = None) -> chat_pb2.ChatEvent:
    error = chat_pb2.ErrorEvent(code=code)
    if detail is not None:
        error.detail = detail
    return chat_pb2.ChatEvent(error=error)


def _final_text_for_done_frame(final_text: str, streamed_text: str) -> str:
    if streamed_text and final_text == streamed_text:
        return ""
    return final_text


def _codex_usage_to_pb(u: CodexUsage) -> chat_pb2.CodexUsage:
    msg = chat_pb2.CodexUsage()
    if u.plan_type:
        msg.plan_type = u.plan_type
    if u.primary is not None:
        msg.primary.CopyFrom(_usage_window_to_pb(u.primary))
    if u.secondary is not None:
        msg.secondary.CopyFrom(_usage_window_to_pb(u.secondary))
    return msg


def _usage_window_to_pb(w: UsageWindow) -> chat_pb2.UsageWindow:
    msg = chat_pb2.UsageWindow(used_percent=w.used_percent, window_minutes=w.window_minutes)
    if w.resets_at is not None:
        msg.resets_at.CopyFrom(to_ts(w.resets_at))
    return msg
