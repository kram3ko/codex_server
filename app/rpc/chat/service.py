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
from app.rpc.chat.uploads import resolve_uploads
from app.services import rate_limit
from app.services.chats import turn_runner, turn_stream
from app.services.chats.default import chat_service
from app.services.codex import turn_registry
from app.services.codex.client import StaleSidecarTurnError
from app.services.codex.error_codes import CodexErrorCode
from app.services.codex.runner import open_codex_turn, quarantine_thread
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
        """Submit new turn, stream events. Decoupled: background asyncio.Task
        робить Codex pump + публікує у Redis Stream; RPC — просто tail-reader.
        Browser disconnect → generator cancelled тут, background продовжує →
        user reconnect через TailTurn піймає live state."""
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

        persisted_chat_id: int | None = None
        bg_owns_release = False
        bg_owns_registry = False
        prelock_acquired = False
        try:
            persisted_chat_id, user_pk = await _ensure_web_chat(user.id)
            if request.HasField("chat_id") and request.chat_id != persisted_chat_id:
                raise ConnectError(Code.NOT_FOUND, f"chat {request.chat_id} not found")

            data_urls, image_ids, audio_ids = await resolve_uploads(
                list(request.upload_ids), user_id=user_pk
            )
            voice_reply = bool(audio_ids)
            has_uploads = bool(data_urls) or bool(audio_ids)

            # Active/pending turn → steer (text-only) або BUSY. Race-safe.
            active = await turn_registry.get(persisted_chat_id)
            if active is not None:
                if active.turn_id is None:
                    log.warning("registry_pending_conflict", chat_id=persisted_chat_id)
                    yield error_event(
                        CodexErrorCode.TURN_BUSY,
                        "another turn is starting for this chat",
                    )
                    return
                if has_uploads:
                    log.warning("registry_active_with_uploads", chat_id=persisted_chat_id)
                    yield error_event(
                        CodexErrorCode.TURN_BUSY,
                        "previous turn still finishing — retry shortly",
                    )
                    return
                accepted = False
                try:
                    accepted = await turn_registry.send_steer(persisted_chat_id, active, text)
                except StaleSidecarTurnError as exc:
                    await _handle_stale_sidecar_turn(persisted_chat_id, active, exc)
                    yield error_event(
                        CodexErrorCode.STALE_ACTIVE_TURN,
                        "previous Codex turn is stale; retry shortly",
                    )
                    return
                except _CONTROL_RPC_ERRORS as exc:
                    log.warning(
                        "web_inline_steer_rpc_failed",
                        chat_id=persisted_chat_id,
                        turn_id=active.turn_id,
                        error=str(exc),
                    )
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
                interrupted = False
                try:
                    interrupted = await turn_registry.send_interrupt(active)
                except StaleSidecarTurnError as exc:
                    await _handle_stale_sidecar_turn(persisted_chat_id, active, exc)
                    yield error_event(
                        CodexErrorCode.STALE_ACTIVE_TURN,
                        "previous Codex turn is stale; retry shortly",
                    )
                    return
                except _CONTROL_RPC_ERRORS as exc:
                    log.warning(
                        "web_inline_interrupt_rpc_failed",
                        chat_id=persisted_chat_id,
                        turn_id=active.turn_id,
                        error=str(exc),
                    )
                if not interrupted:
                    yield error_event(
                        CodexErrorCode.TURN_BUSY,
                        "previous turn still finishing — retry shortly",
                    )
                    return
                await turn_registry.drop_if_matches(
                    persisted_chat_id, active.thread_id, active.turn_id
                )

            # Synchronously захоплюємо registry slot ДО persist + spawn. Закриває:
            # (1) гонку де два concurrent RunTurn-и обидва бачили б `active=None`
            #     і обидва писали б user message + спавнили WS-и;
            # (2) orphan user message у БД від loser-а який потім падає BUSY.
            # Placeholder thread_id=None — реальний thread_id виставиться
            # `promote_pending` у `_on_started` коли codex поверне turn_id.
            registered = await turn_registry.try_register_pending(
                persisted_chat_id, thread_id=None, is_admin=True
            )
            if not registered:
                yield error_event(
                    CodexErrorCode.TURN_BUSY,
                    "another turn is starting for this chat",
                )
                return
            prelock_acquired = True

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

            # Чистий стрім перед новим turn-ом → tail(after_id="0") catch-нe
            # кожен XADD від background без race з `$`-cursor проти першого
            # publish-у. Альтернатива (per-turn stream key) вимагала б proto-зміну.
            await turn_stream.reset(persisted_chat_id)

            # Decouple: turn live'ає в background task, переживає browser disconnect.
            # RPC = просто reader Redis Stream'у; rate-limit release ownership
            # передається background'у.
            ready = turn_runner.spawn(
                chat_id=persisted_chat_id,
                user_pk=user_pk,
                rl_user=user,
                text=text,
                image_urls=data_urls,
                voice_reply=voice_reply,
                client_id=request.client_id or None,
                is_admin=True,
            )
            bg_owns_release = True
            bg_owns_registry = True

            # Wait WS handshake done; `finally: ready.set()` у `_run` гарантує
            # що signal прийде навіть на crash.
            await ready.wait()

            async for event in turn_stream.tail(persisted_chat_id, after_id="0"):
                yield event
        finally:
            if not bg_owns_release:
                await rate_limit.release_turn(user)
            # До spawn ownership ще у RPC handler-а: якщо впали після pre-lock,
            # але до передачі background task-у, чистимо placeholder тут.
            # Після spawn registry cleanup належить `turn_runner`, і RPC reader
            # не має права знімати lock при browser disconnect.
            if prelock_acquired and not bg_owns_registry and persisted_chat_id is not None:
                with contextlib.suppress(Exception):
                    await turn_registry.drop_if_matches(
                        persisted_chat_id, thread_id=None, turn_id=None
                    )

    @override
    async def tail_turn(
        self,
        request: chat_pb2.TailTurnRequest,
        ctx: RequestContext,
    ) -> AsyncIterator[chat_pb2.ChatEvent]:
        """Resume mid-turn stream: replay'ить XREAD-buffer з `after_id`, потім
        BLOCK на нові події доки `done`/`error` не прийде або stream не зникне.
        """
        user = await require_user(ctx)
        async with SessionLocal() as db:
            await load_chat_owned(db, request.chat_id, user.id)
        async for event in turn_stream.tail(request.chat_id, request.after_id):
            yield event

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
        except StaleSidecarTurnError as exc:
            await _handle_stale_sidecar_turn(chat.id, record, exc)
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
        except StaleSidecarTurnError as exc:
            await _handle_stale_sidecar_turn(chat.id, record, exc)
            return chat_pb2.SteerTurnResponse(accepted=False)
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


async def _handle_stale_sidecar_turn(
    chat_id: int,
    active: turn_registry.ActiveTurn,
    exc: StaleSidecarTurnError,
) -> None:
    """Break registry/sidecar mismatch without spawning another doomed turn."""
    log.warning(
        "web_stale_sidecar_turn",
        chat_id=chat_id,
        registry_thread_id=active.thread_id,
        registry_turn_id=active.turn_id,
        expected_turn_id=exc.expected_turn_id,
        actual_turn_id=exc.actual_turn_id,
    )
    with contextlib.suppress(Exception):
        await turn_registry.send_interrupt_turn_id(active.is_admin, exc.actual_turn_id)
    await quarantine_thread(active.thread_id)
    await turn_registry.drop_if_matches(chat_id, active.thread_id, active.turn_id)
