"""ChatService — chat CRUD plus web turn streaming."""

import asyncio
import contextlib
from collections.abc import AsyncIterator
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
from app.rpc._mappers import chat_to_pb, to_struct
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
from app.services.events.default import event_service
from app.services.messages.default import message_service
from app.services.sessions.store import ChatSession
from app.services.sessions.web import web_sessions
from app.services.uploads.default import upload_service

_DEFAULT_LIMIT = 50
_MAX_LIMIT = 200

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
        async with SessionLocal() as db:
            await message_service.append(db, persisted_chat_id, MessageRole.USER, text)
            await event_service.emit(
                db,
                EventKind.TURN_STARTED,
                chat_id=persisted_chat_id,
                user_id=user_pk,
                payload={"text_len": len(text)},
            )
            await db.commit()

        async with session.turn_lock:
            await web_sessions.seed_history_if_fresh_thread(session)
            current = asyncio.current_task()
            session.current_turn_task = current
            try:
                async for event in _stream_turn(session, text, persisted_chat_id, user_pk):
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


def _resolve_limit(p: common_pb2.Pagination) -> int:
    limit = p.limit if p.limit > 0 else _DEFAULT_LIMIT
    return min(limit, _MAX_LIMIT)


async def _load_chat_owned(db, chat_id: int, user_id: int) -> Chat:
    chat = await chat_service.get(db, chat_id)
    if chat is None or chat.user_id != user_id:
        raise ConnectError(Code.NOT_FOUND, f"chat {chat_id} not found")
    return chat


async def _stream_turn(
    session: ChatSession,
    text: str,
    persisted_chat_id: int,
    user_pk: int,
) -> AsyncIterator[chat_pb2.ChatEvent]:
    final_text = ""
    streamed_text = ""
    tool_calls: list[ToolCallRecord] = []
    attachments: list[Attachment] = []
    done_seen = False
    stream = session.client.run_turn(text)
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
                pb.error = error
            return chat_pb2.ChatEvent(tool_result=pb)
        case ErrorEvent(code=code, detail=detail):
            return _error_event(code, detail)
        case DoneEvent(final_text=final_text):
            return chat_pb2.ChatEvent(done=chat_pb2.DoneEvent(final_text=final_text))
        case _:
            return _error_event("unknown_event", type(event).__name__)


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
