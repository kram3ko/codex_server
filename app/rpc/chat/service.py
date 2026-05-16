"""ChatRPC — тонкі handler'и для ChatService.

Per-turn lifecycle: кожен `run_turn` відкриває fresh Codex WebSocket → handshake
→ resume/start thread → стрім → close. Між RPC викликами state'у in-process нема.

Interrupt/Steer працюють cross-worker через Redis `turn_registry`: будь-який
воркер бачить активний turn'а і шле `turn/interrupt|steer` у sidecar одноразовою
WS. Сумісно з `gunicorn -w N`.
"""

import contextlib
from collections.abc import AsyncIterator
from typing import Any, override

import structlog
import websockets
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
from app.services import rate_limit
from app.services.chats.default import chat_service
from app.services.codex import turn_registry
from app.services.codex.error_codes import CodexErrorCode
from app.services.codex.runner import open_codex_turn
from app.services.codex.transport import AppServerError
from app.services.codex_usage.default import codex_usage_service
from app.services.events.default import event_service
from app.services.messages.default import message_service

# Cross-worker control RPC до Codex sidecar (interrupt/steer) — типова мережа:
# OS-level (WSL/Docker), WS-protocol (handshake/frames), JSON-RPC error від
# самого сервера, або сам connect timeout'нувся.
_CONTROL_RPC_ERRORS = (
    AppServerError,
    websockets.WebSocketException,
    OSError,
    TimeoutError,
)

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
            yield error_event(CodexErrorCode.EMPTY_TEXT, "text is required")
            return

        try:
            await rate_limit.reserve_turn(user)
        except rate_limit.RateLimited as exc:
            yield error_event(CodexErrorCode.RATE_LIMITED, str(exc))
            return

        try:
            async for ev in self._run_turn_body(request, user, text):
                yield ev
        finally:
            await rate_limit.release_turn(user)

    async def _run_turn_body(
        self,
        request: chat_pb2.RunTurnRequest,
        user: Any,
        text: str,
    ) -> AsyncIterator[chat_pb2.ChatEvent]:
        persisted_chat_id, user_pk = await _ensure_web_chat(user.id)
        if request.HasField("chat_id") and request.chat_id != persisted_chat_id:
            raise ConnectError(Code.NOT_FOUND, f"chat {request.chat_id} not found")

        data_urls, image_ids, audio_ids = await resolve_uploads(
            list(request.upload_ids), user_id=user_pk
        )
        voice_reply = bool(audio_ids)
        has_uploads = bool(data_urls) or bool(audio_ids)

        # Guard: active/pending turn → server-side steer / BUSY
        # (race-safe для multi-tab / direct RPC).
        active = await turn_registry.get(persisted_chat_id)
        if active is not None:
            if active.turn_id is None:
                # Pending — інший воркер у вікні turn/start. Reject as busy.
                log.warning("registry_pending_conflict", chat_id=persisted_chat_id)
                yield error_event(
                    CodexErrorCode.TURN_BUSY, "another turn is starting for this chat"
                )
                return
            if has_uploads:
                # Uploads + active → BUSY; client має сам interrupt + retry
                # (sidecar overlap risk inline).
                log.warning("registry_active_with_uploads", chat_id=persisted_chat_id)
                yield error_event(
                    CodexErrorCode.TURN_BUSY,
                    "previous turn still finishing — retry shortly",
                )
                return
            # Text-only + active: cross-worker steer у running turn.
            accepted = False
            with contextlib.suppress(Exception):
                accepted = await turn_registry.send_steer(persisted_chat_id, active, text)
            if accepted:
                async with SessionLocal() as db:
                    await message_service.append(
                        db,
                        persisted_chat_id,
                        MessageRole.USER,
                        text,
                        meta={"steered": True},
                    )
                    await db.commit()
                yield chat_pb2.ChatEvent(
                    done=chat_pb2.DoneEvent(
                        chat_id=persisted_chat_id,
                        final_text="",
                        steered_fallback=True,
                    )
                )
                return
            # Steer rejected → turn закінчився між get і send. Drop stale CAS-safely
            # і впадаємо у новий turn нижче.
            await turn_registry.drop_if_matches(persisted_chat_id, active.thread_id, active.turn_id)

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
                    client_id=request.client_id or None,
                ):
                    yield event
            finally:
                # CAS-drop: silent miss — нормально після interrupt'у (запис уже стертий).
                await turn_registry.drop_if_matches(
                    persisted_chat_id,
                    client.current_thread_id or "",
                    client.current_turn_id,
                )

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
        if record.turn_id is None:
            # Pending — CAS-drop сигналізує worker'у-власнику турна через
            # promote_pending=False. Якщо owner встиг promote-нути між нашим
            # get і drop — no-op, активний turn лишається живим (юзер може
            # повторити interrupt уже на promoted record).
            await turn_registry.drop_if_matches(chat.id, record.thread_id, None)
            return chat_pb2.InterruptTurnResponse()
        try:
            await turn_registry.send_interrupt(record)
        except _CONTROL_RPC_ERRORS as exc:
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
        user = await require_user(ctx)
        text = request.text.strip()
        if not text:
            return chat_pb2.SteerTurnResponse(accepted=False)
        async with SessionLocal() as db:
            chat = await load_chat_owned(db, request.chat_id, user.id)
        record = await turn_registry.get(chat.id)
        if record is None:
            return chat_pb2.SteerTurnResponse(accepted=False)
        try:
            accepted = await turn_registry.send_steer(chat.id, record, text)
        except _CONTROL_RPC_ERRORS as exc:
            log.warning("web_steer_rpc_failed", chat_id=chat.id, error=str(exc))
            return chat_pb2.SteerTurnResponse(accepted=False)
        if accepted:
            async with SessionLocal() as db:
                await message_service.append(
                    db, chat.id, MessageRole.USER, text, meta={"steered": True}
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
        user = await require_user(ctx)
        # Open fresh client just to read rate-limits; cheap (handshake only).
        persisted_chat_id, _ = await _ensure_web_chat(user.id)
        async with open_codex_turn(persisted_chat_id, is_admin=True, seed_history=False) as client:
            usage = await codex_usage_service.latest(client)
        if usage is None:
            return chat_pb2.CodexUsage()
        return codex_usage_to_pb(usage)


async def _ensure_web_chat(user_id: int) -> tuple[int, int]:
    async with SessionLocal() as db:
        chat = await chat_service.get_or_create_for_web(db, user_id)
        await db.commit()
        return chat.id, user_id
