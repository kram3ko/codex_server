"""ChatRPC — тонкі handler'и для ChatService. Turn-as-a-Job: handler створює
`turns` row + спавнить bg runner; стрім читається з per-turn Redis stream."""

import contextlib
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, override

import structlog
import websockets
from connectrpc.code import Code
from connectrpc.errors import ConnectError
from connectrpc.request import RequestContext
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import SessionLocal
from app.grpc_generated.codex.v1 import chat_pb2, common_pb2
from app.grpc_generated.codex.v1.chat_connect import ChatService as ChatProtocol
from app.models import (
    TURN_TERMINAL_STATUSES,
    EventKind,
    Message,
    MessageRole,
    Turn,
    TurnStatus,
    UserRole,
)
from app.rpc._auth import require_user
from app.rpc._mappers import chat_to_pb, message_to_pb
from app.rpc.chat.guards import load_chat_owned, resolve_limit
from app.rpc.chat.mappers import codex_usage_to_pb, error_event
from app.rpc.chat.uploads import resolve_uploads
from app.services import rate_limit
from app.services.cache.default import cache
from app.services.chats.default import chat_service
from app.services.codex import codex_remote
from app.services.codex.client import StaleSidecarTurnError
from app.services.codex.error_codes import CodexErrorCode
from app.services.codex.runner import quarantine_thread
from app.services.codex.sidecar import SidecarName
from app.services.codex.transport import AppServerError
from app.services.codex_usage import poller as usage_poller
from app.services.events.default import event_service
from app.services.messages.default import message_service
from app.services.turns.default import turn_service, turn_stream
from app.services.turns.recovery import reconcile_if_stale
from app.services.turns.schemas import TurnCreate, TurnRow
from app.services.turns.tasks import execute_turn

# OS / WS / JSON-RPC / connect-timeout — типовий network-помилковий пакет.
_CONTROL_RPC_ERRORS = (
    AppServerError,
    websockets.WebSocketException,
    OSError,
    TimeoutError,
)

log = structlog.get_logger(__name__)


@dataclass(frozen=True)
class _PreparedRunTurn:
    chat_id: int
    user_id: int
    text: str
    data_urls: tuple[str, ...]
    image_ids: list[int]
    audio_ids: list[int]
    voice_reply: bool
    has_uploads: bool
    sidecar: SidecarName
    client_id: str | None


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

        worker_owns_rate_limit = False
        try:
            prepared = await _prepare_run_turn(request, text, user.id)

            active_event = await _handle_active_turn(prepared)
            if active_event is not None:
                yield active_event
                return

            turn, create_error = await _create_web_turn(prepared)
            if create_error is not None:
                yield create_error
                return
            assert turn is not None  # narrowed by union discriminator вище

            enqueue_error = await _enqueue_turn(turn, prepared)
            if enqueue_error is not None:
                yield enqueue_error
                return
            worker_owns_rate_limit = True

            yield chat_pb2.ChatEvent(turn_started=chat_pb2.TurnStartedEvent(turn_id=turn.id))

            async for event in turn_stream.tail(turn.id, ""):
                yield event

            terminal = await _terminal_for_turn_id(turn.id)
            if terminal is not None:
                yield terminal
        finally:
            if not worker_owns_rate_limit:
                await rate_limit.release_turn(user)

    @override
    async def tail_turn(
        self,
        request: chat_pb2.TailTurnRequest,
        ctx: RequestContext,
    ) -> AsyncIterator[chat_pb2.ChatEvent]:
        """Resume per-turn stream. Якщо `turn_id` set — tail саме його; інакше
        legacy шлях через `get_active_for_chat(chat_id)`."""
        user = await require_user(ctx)
        target_turn: TurnRow | None = None
        if request.HasField("turn_id"):
            async with SessionLocal() as db:
                target_turn = await turn_service.get_by_id(db, request.turn_id)
            if target_turn is None:
                raise ConnectError(Code.NOT_FOUND, f"turn {request.turn_id} not found")
            async with SessionLocal() as db:
                await load_chat_owned(db, target_turn.chat_id, user.id)
        else:
            async with SessionLocal() as db:
                await load_chat_owned(db, request.chat_id, user.id)
                target_turn = await turn_service.get_active_for_chat(db, request.chat_id)
            if target_turn is None:
                async with SessionLocal() as db:
                    latest = await _latest_turn_for_chat(db, request.chat_id)
                if latest is not None and latest.status in TURN_TERMINAL_STATUSES:
                    terminal = await _terminal_from_status(latest)
                    if terminal is not None:
                        yield terminal
                return

        if target_turn.status in TURN_TERMINAL_STATUSES:
            terminal = await _terminal_from_status(target_turn)
            if terminal is not None:
                yield terminal
            return

        async for event in turn_stream.tail(target_turn.id, request.after_id):
            yield event
        async with SessionLocal() as db:
            refreshed = await turn_service.get_by_id(db, target_turn.id)
        if refreshed is not None and refreshed.status in TURN_TERMINAL_STATUSES:
            terminal = await _terminal_from_status(refreshed)
            if terminal is not None:
                yield terminal

    @override
    async def interrupt_turn(
        self,
        request: chat_pb2.InterruptTurnRequest,
        ctx: RequestContext,
    ) -> chat_pb2.InterruptTurnResponse:
        user = await require_user(ctx)
        async with SessionLocal() as db:
            chat = await load_chat_owned(db, request.chat_id, user.id)
            active = await turn_service.get_active_for_chat(db, chat.id)
        if active is None or active.codex_turn_id is None:
            return chat_pb2.InterruptTurnResponse()
        try:
            await codex_remote.send_interrupt_turn_id(
                SidecarName.normalize(active.sidecar) is SidecarName.ADMIN,
                active.codex_turn_id,
            )
        except StaleSidecarTurnError as exc:
            await _handle_stale_turn(chat.id, active, exc)
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
            active = await turn_service.get_active_for_chat(db, chat.id)
        if active is None or active.codex_turn_id is None:
            return chat_pb2.SteerTurnResponse(accepted=False)
        try:
            accepted = await codex_remote.send_steer_by_ids(
                chat_id=chat.id,
                is_admin=SidecarName.normalize(active.sidecar) is SidecarName.ADMIN,
                thread_id=active.codex_thread_id,
                codex_turn_id=active.codex_turn_id,
                text=text,
            )
        except StaleSidecarTurnError as exc:
            await _handle_stale_turn(chat.id, active, exc)
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
            # Bump ПІСЛЯ persist — гарантує що stream-loop persist-segment
            # завжди має id > USER steer message id (chronology stable).
            await codex_remote.bump_steer_count(active.id)
        return chat_pb2.SteerTurnResponse(accepted=accepted)

    @override
    async def stream_codex_usage(
        self,
        request: chat_pb2.StreamCodexUsageRequest,
        ctx: RequestContext,
    ) -> AsyncIterator[chat_pb2.CodexUsage]:
        del request
        user = await require_user(ctx)
        sidecar = SidecarName.ADMIN if user.role == UserRole.ADMIN else SidecarName.GUEST
        channel = usage_poller.channel_for(sidecar)
        snapshot_key = usage_poller.snapshot_key(sidecar)

        # Subscribe ПЕРЕД read snapshot щоб publish між цими двома операціями
        # не загубився (raceless bootstrap). Перший frame завжди yield-имо —
        # `CodexUsage()` empty якщо snapshot ще нема (UI вийде з "loading" у
        # "no data" замість вічного spinner).
        pubsub = cache.pubsub()
        await pubsub.subscribe(channel)
        try:
            raw = await cache.get(snapshot_key)
            bootstrap_usage = (
                usage_poller.deserialize(raw) if raw is not None else None
            )
            yield (
                codex_usage_to_pb(bootstrap_usage)
                if bootstrap_usage is not None
                else chat_pb2.CodexUsage()
            )
            async for message in pubsub.listen():
                if message.get("type") != "message":
                    continue
                usage = usage_poller.deserialize(message["data"])
                if usage is not None:
                    yield codex_usage_to_pb(usage)
        finally:
            with contextlib.suppress(Exception):
                await pubsub.unsubscribe(channel)
                await pubsub.aclose()

    @override
    async def refresh_codex_usage(
        self,
        request: chat_pb2.RefreshCodexUsageRequest,
        ctx: RequestContext,
    ) -> chat_pb2.CodexUsage:
        del request
        user = await require_user(ctx)
        sidecar = SidecarName.ADMIN if user.role == UserRole.ADMIN else SidecarName.GUEST
        # Sync fetch + publish (publish_for пише cache + pub/sub) — інші
        # підписані StreamCodexUsage клієнти теж отримають свіжий snapshot.
        await usage_poller.publish_for(sidecar)
        raw = await cache.get(usage_poller.snapshot_key(sidecar))
        if raw is None:
            return chat_pb2.CodexUsage()
        usage = usage_poller.deserialize(raw)
        if usage is None:
            return chat_pb2.CodexUsage()
        return codex_usage_to_pb(usage)


async def _ensure_web_chat(user_id: int) -> tuple[int, int]:
    async with SessionLocal() as db:
        chat = await chat_service.get_or_create_for_web(db, user_id)
        await db.commit()
        return chat.id, user_id


async def _prepare_run_turn(
    request: chat_pb2.RunTurnRequest,
    text: str,
    user_id: int,
) -> _PreparedRunTurn:
    chat_id, user_pk = await _ensure_web_chat(user_id)
    if request.HasField("chat_id") and request.chat_id != chat_id:
        raise ConnectError(Code.NOT_FOUND, f"chat {request.chat_id} not found")

    data_urls, image_ids, audio_ids = await resolve_uploads(
        list(request.upload_ids), user_id=user_pk
    )
    return _PreparedRunTurn(
        chat_id=chat_id,
        user_id=user_pk,
        text=text,
        data_urls=tuple(data_urls),
        image_ids=image_ids,
        audio_ids=audio_ids,
        voice_reply=bool(audio_ids),
        has_uploads=bool(data_urls) or bool(audio_ids),
        sidecar=SidecarName.ADMIN,
        client_id=request.client_id or None,
    )


async def _handle_active_turn(prepared: _PreparedRunTurn) -> chat_pb2.ChatEvent | None:
    async with SessionLocal() as db:
        active = await turn_service.get_active_for_chat(db, prepared.chat_id)
    if active is not None and await reconcile_if_stale(active):
        active = None
    if active is None:
        return None

    if active.codex_turn_id is None:
        log.warning("turn_active_pending_conflict", chat_id=prepared.chat_id)
        return error_event(
            CodexErrorCode.TURN_BUSY,
            "another turn is starting for this chat",
        )
    if prepared.has_uploads:
        log.warning("turn_active_with_uploads", chat_id=prepared.chat_id)
        return error_event(
            CodexErrorCode.TURN_BUSY,
            "previous turn still finishing — retry shortly",
        )

    steer_event = await _try_steer_active_turn(prepared, active)
    if steer_event is not None:
        return steer_event

    interrupted, interrupt_event = await _try_interrupt_active_turn(prepared.chat_id, active)
    if interrupt_event is not None:
        return interrupt_event
    if not interrupted:
        return error_event(
            CodexErrorCode.TURN_BUSY,
            "previous turn still finishing — retry shortly",
        )
    return None


async def _try_steer_active_turn(
    prepared: _PreparedRunTurn,
    active: TurnRow,
) -> chat_pb2.ChatEvent | None:
    # Caller `_handle_active_turn` гарантує що codex_turn_id вже set.
    assert active.codex_turn_id is not None
    try:
        accepted = await codex_remote.send_steer_by_ids(
            chat_id=prepared.chat_id,
            is_admin=SidecarName.normalize(active.sidecar) is SidecarName.ADMIN,
            thread_id=active.codex_thread_id,
            codex_turn_id=active.codex_turn_id,
            text=prepared.text,
        )
    except StaleSidecarTurnError as exc:
        await _handle_stale_turn(prepared.chat_id, active, exc)
        return error_event(
            CodexErrorCode.STALE_ACTIVE_TURN,
            "previous Codex turn is stale; retry shortly",
        )
    except _CONTROL_RPC_ERRORS as exc:
        log.warning(
            "web_inline_steer_rpc_failed",
            chat_id=prepared.chat_id,
            turn_id=active.codex_turn_id,
            error=str(exc),
        )
        return None

    if not accepted:
        return None

    async with SessionLocal() as db:
        await message_service.append(
            db,
            prepared.chat_id,
            MessageRole.USER,
            prepared.text,
            meta={"steered": True},
        )
        await db.commit()
    # Bump steer-count тільки після persist USER row — гарантує stable
    # chronology у stream-loop persist_segment.
    await codex_remote.bump_steer_count(active.id)
    return chat_pb2.ChatEvent(
        done=chat_pb2.DoneEvent(
            chat_id=prepared.chat_id,
            final_text="",
            steered_fallback=True,
        )
    )


async def _try_interrupt_active_turn(
    chat_id: int,
    active: TurnRow,
) -> tuple[bool, chat_pb2.ChatEvent | None]:
    assert active.codex_turn_id is not None  # caller-narrowed
    try:
        interrupted = await codex_remote.send_interrupt_turn_id(
            SidecarName.normalize(active.sidecar) is SidecarName.ADMIN, active.codex_turn_id
        )
        return interrupted, None
    except StaleSidecarTurnError as exc:
        await _handle_stale_turn(chat_id, active, exc)
        return False, error_event(
            CodexErrorCode.STALE_ACTIVE_TURN,
            "previous Codex turn is stale; retry shortly",
        )
    except _CONTROL_RPC_ERRORS as exc:
        log.warning(
            "web_inline_interrupt_rpc_failed",
            chat_id=chat_id,
            turn_id=active.codex_turn_id,
            error=str(exc),
        )
        return False, None


async def _create_web_turn(
    prepared: _PreparedRunTurn,
) -> tuple[TurnRow, None] | tuple[None, chat_pb2.ChatEvent]:
    user_meta = _user_message_meta(prepared)
    async with SessionLocal() as db:
        user_message = await message_service.append(
            db,
            prepared.chat_id,
            MessageRole.USER,
            prepared.text,
            meta=user_meta,
        )
        turn = await turn_service.try_create_starting(
            db,
            TurnCreate(
                chat_id=prepared.chat_id,
                user_id=prepared.user_id,
                user_message_id=user_message.id,
                sidecar=prepared.sidecar,
            ),
        )
        if turn is None:
            log.warning("turn_create_race_lost", chat_id=prepared.chat_id)
            return None, error_event(
                CodexErrorCode.TURN_BUSY,
                "another turn is starting for this chat",
            )
        await event_service.emit(
            db,
            EventKind.TURN_STARTED,
            chat_id=prepared.chat_id,
            user_id=prepared.user_id,
            payload={
                "turn_id": turn.id,
                "text_len": len(prepared.text),
                "attachments": len(prepared.data_urls),
            },
        )
        await db.commit()
        return turn, None


def _user_message_meta(prepared: _PreparedRunTurn) -> dict[str, Any] | None:
    meta: dict[str, Any] = {}
    if prepared.image_ids:
        meta["upload_ids"] = prepared.image_ids
    if prepared.audio_ids:
        meta["audio_upload_ids"] = prepared.audio_ids
    return meta or None


async def _enqueue_turn(
    turn: TurnRow,
    prepared: _PreparedRunTurn,
) -> chat_pb2.ChatEvent | None:
    try:
        await execute_turn.kiq(
            turn.id,
            text=prepared.text,
            image_urls=list(prepared.data_urls),
            voice_reply=prepared.voice_reply,
            client_id=prepared.client_id,
        )
    except Exception as exc:
        log.exception("turn_enqueue_failed", turn_id=turn.id)
        async with SessionLocal() as db:
            await turn_service.finalize_once(
                db,
                turn.id,
                TurnStatus.FAILED,
                error_code="enqueue_failed",
                error_detail=str(exc),
            )
            await db.commit()
        return error_event(CodexErrorCode.CODEX_ERROR, f"enqueue failed: {exc}")
    return None


async def _terminal_for_turn_id(turn_id: int) -> chat_pb2.ChatEvent | None:
    async with SessionLocal() as db:
        turn = await turn_service.get_by_id(db, turn_id)
    if turn is None or turn.status not in TURN_TERMINAL_STATUSES:
        return None
    return await _terminal_from_status(turn)


async def _handle_stale_turn(
    chat_id: int,
    turn: TurnRow,
    exc: StaleSidecarTurnError,
) -> None:
    """Interrupt actual codex turn → quarantine thread → finalize FAILED."""
    log.warning(
        "web_stale_sidecar_turn",
        chat_id=chat_id,
        turn_id=turn.id,
        expected_turn_id=exc.expected_turn_id,
        actual_turn_id=exc.actual_turn_id,
    )
    with contextlib.suppress(Exception):
        await codex_remote.send_interrupt_turn_id(
            SidecarName.normalize(turn.sidecar) is SidecarName.ADMIN, exc.actual_turn_id
        )
    await quarantine_thread(turn.codex_thread_id)
    async with SessionLocal() as db:
        await turn_service.finalize_once(
            db,
            turn.id,
            TurnStatus.FAILED,
            error_code=CodexErrorCode.STALE_ACTIVE_TURN,
            error_detail=f"sidecar held {exc.actual_turn_id}",
        )
        await db.commit()


async def _terminal_from_status(turn: TurnRow) -> chat_pb2.ChatEvent | None:
    """Synthesize terminal ChatEvent з `turn.status` для UI close-placeholder.
    На COMPLETED додаємо persisted assistant message прямо у DoneEvent —
    клієнту не треба робити окремий reload щоб дізнатися фінальний row."""
    if turn.status == TurnStatus.COMPLETED:
        done = chat_pb2.DoneEvent(chat_id=turn.chat_id, final_text="")
        if turn.assistant_message_id is not None:
            async with SessionLocal() as db:
                msg = await db.get(Message, turn.assistant_message_id)
            if msg is not None:
                done.message.CopyFrom(message_to_pb(msg))
                done.final_text = msg.text
        return chat_pb2.ChatEvent(done=done)
    if turn.status in (TurnStatus.FAILED, TurnStatus.CANCELLED):
        code = turn.error_code or (
            CodexErrorCode.STREAM_DROPPED if turn.status == TurnStatus.FAILED else "cancelled"
        )
        return error_event(code, turn.error_detail or turn.status.value.lower())
    return None


async def _latest_turn_for_chat(db: AsyncSession, chat_id: int) -> TurnRow | None:
    """Найсвіжіший turn для chat (terminal-replay після reconnect)."""
    row = await db.execute(
        select(Turn).where(Turn.chat_id == chat_id).order_by(Turn.id.desc()).limit(1)
    )
    turn = row.scalar_one_or_none()
    return TurnRow.model_validate(turn) if turn is not None else None
