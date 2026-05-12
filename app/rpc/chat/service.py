"""ChatRPC — тонкі handler'и для ChatService.

Per-turn lifecycle: кожен `run_turn` відкриває fresh Codex WebSocket → handshake
→ resume/start thread → стрім → close. Між RPC викликами state'у in-process нема.

Interrupt/Steer працюють cross-worker через Redis `turn_registry`: будь-який
воркер бачить активний turn'а і шле `turn/interrupt|steer` у sidecar одноразовою
WS. Сумісно з `gunicorn -w N`.
"""

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
from app.services.codex import turn_registry
from app.services.codex.error_codes import CodexErrorCode
from app.services.codex.runner import open_codex_turn
from app.services.codex_usage.default import codex_usage_service
from app.services.events.default import event_service
from app.services.messages.default import message_service
from app.services.users.default import user_service

log = structlog.get_logger(__name__)

_WEB_USER_EMAIL = "web@codex.local"


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
        await require_user(ctx)
        text = request.text.strip()
        if not text:
            yield error_event(CodexErrorCode.EMPTY_TEXT, "text is required")
            return

        persisted_chat_id, user_pk = await _ensure_web_chat()
        if request.HasField("chat_id") and request.chat_id != persisted_chat_id:
            raise ConnectError(Code.NOT_FOUND, f"chat {request.chat_id} not found")

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

        async with open_codex_turn(persisted_chat_id, is_admin=True) as client:
            try:
                async for event in stream_turn(
                    client,
                    text,
                    persisted_chat_id,
                    user_pk,
                    image_urls=data_urls,
                    voice_reply=voice_reply,
                ):
                    yield event
            finally:
                await turn_registry.drop(persisted_chat_id)

    @override
    async def interrupt_turn(
        self,
        request: chat_pb2.InterruptTurnRequest,
        ctx: RequestContext,
    ) -> chat_pb2.InterruptTurnResponse:
        user = await require_user(ctx)
        async with SessionLocal() as db:
            chat = await load_chat_owned(db, request.chat_id, user.id)
        record = await turn_registry.get(chat.id)
        if record is None:
            return chat_pb2.InterruptTurnResponse()
        try:
            await turn_registry.send_interrupt(record)
        except Exception as exc:  # noqa: BLE001 — interrupt best-effort
            log.warning(
                "web_interrupt_rpc_failed",
                chat_id=chat.id,
                user_id=user.id,
                error=str(exc),
            )
        return chat_pb2.InterruptTurnResponse()

    @override
    async def steer_turn(
        self,
        request: chat_pb2.SteerTurnRequest,
        ctx: RequestContext,
    ) -> chat_pb2.SteerTurnResponse:
        await require_user(ctx)
        text = request.text.strip()
        if not text:
            return chat_pb2.SteerTurnResponse(accepted=False)
        record = await turn_registry.get(request.chat_id)
        if record is None:
            return chat_pb2.SteerTurnResponse(accepted=False)
        accepted = await turn_registry.send_steer(record, text)
        if accepted:
            async with SessionLocal() as db:
                await message_service.append(
                    db, request.chat_id, MessageRole.USER, text
                )
                await db.commit()
        return chat_pb2.SteerTurnResponse(accepted=accepted)

    @override
    async def get_codex_usage(
        self,
        request: chat_pb2.GetCodexUsageRequest,
        ctx: RequestContext,
    ) -> chat_pb2.CodexUsage:
        del request
        await require_user(ctx)
        # Open fresh client just to read rate-limits; cheap (handshake only).
        persisted_chat_id, _ = await _ensure_web_chat()
        async with open_codex_turn(persisted_chat_id, is_admin=True, seed_history=False) as client:
            usage = await codex_usage_service.latest(client)
        if usage is None:
            return chat_pb2.CodexUsage()
        return codex_usage_to_pb(usage)


async def _ensure_web_chat() -> tuple[int, int]:
    async with SessionLocal() as db:
        web_user = await user_service.get_or_create_by_email(db, _WEB_USER_EMAIL)
        chat = await chat_service.get_or_create_for_web(db, web_user.id)
        await db.commit()
        return chat.id, web_user.id
