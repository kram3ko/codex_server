"""ChatRPC — тонкі handler'и для ChatService.

CRUD'и пагінуються через `guards.resolve_limit`. `run_turn` делегує streaming-
pipeline у `stream.stream_turn`, тут лише: auth, lock, persist user message,
emit TURN_STARTED.
"""

import asyncio
from collections.abc import AsyncIterator
from typing import Any, override

import structlog
from connectrpc.code import Code
from connectrpc.errors import ConnectError
from connectrpc.request import RequestContext

from app.db.base import SessionLocal
from app.grpc_generated.codex.v1 import chat_pb2, common_pb2
from app.grpc_generated.codex.v1.chat_connect import ChatService as ChatProtocol
from app.models import EventKind, MessageRole
from app.rpc._auth import require_user
from app.rpc._mappers import chat_to_pb
from app.rpc.chat.guards import load_chat_owned, resolve_limit
from app.rpc.chat.mappers import codex_usage_to_pb, error_event
from app.rpc.chat.stream import stream_turn
from app.rpc.chat.uploads import resolve_uploads
from app.services.chats.default import chat_service
from app.services.codex_usage.default import codex_usage_service
from app.services.events.default import event_service
from app.services.messages.default import message_service
from app.services.sessions.web import web_sessions

log = structlog.get_logger(__name__)


class ChatRPC(ChatProtocol):
    @override
    async def list_chats(
        self,
        request: chat_pb2.ListChatsRequest,
        ctx: RequestContext,
    ) -> chat_pb2.ListChatsResponse:
        user = await require_user(ctx)
        limit = resolve_limit(request.pagination)
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
            chat = await load_chat_owned(db, request.chat_id, user.id)
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
            chat = await load_chat_owned(db, request.chat_id, user.id)
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
            chat = await load_chat_owned(db, request.chat_id, user.id)
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
            yield error_event("empty_text", "text is required")
            return

        session = await web_sessions.get_or_open(user.id)
        if request.HasField("chat_id") and request.chat_id != session.db_chat_id:
            raise ConnectError(Code.NOT_FOUND, f"chat {request.chat_id} not found")

        persisted_chat_id = session.db_chat_id
        user_pk = session.db_user_id
        data_urls, image_ids, audio_ids = await resolve_uploads(
            list(request.upload_ids), user_id=user_pk
        )
        voice_reply = bool(audio_ids)
        user_meta: dict[str, Any] = {}
        if image_ids:
            user_meta["upload_ids"] = image_ids
        if audio_ids:
            user_meta["audio_upload_ids"] = audio_ids
        async with SessionLocal() as db:
            await message_service.append(
                db, persisted_chat_id, MessageRole.USER, text, meta=user_meta or None
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
                async for event in stream_turn(
                    session,
                    text,
                    persisted_chat_id,
                    user_pk,
                    image_urls=data_urls,
                    voice_reply=voice_reply,
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
            chat = await load_chat_owned(db, request.chat_id, user.id)
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
            log.warning("steer_rejected", user_id=user.id, reason="empty_text")
            return chat_pb2.SteerTurnResponse(accepted=False)
        session = await web_sessions.get(user.id)
        if session is None:
            log.warning("steer_rejected", user_id=user.id, reason="no_session")
            return chat_pb2.SteerTurnResponse(accepted=False)
        if session.db_chat_id != request.chat_id:
            log.warning(
                "steer_rejected",
                user_id=user.id,
                reason="chat_mismatch",
                requested=request.chat_id,
                session_chat=session.db_chat_id,
            )
            return chat_pb2.SteerTurnResponse(accepted=False)
        accepted = await session.client.steer(text)
        if not accepted:
            log.warning("steer_rejected", user_id=user.id, reason="sidecar_rejected")
        else:
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
        return codex_usage_to_pb(usage)
