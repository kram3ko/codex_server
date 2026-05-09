"""WebSocket /chat/ws — bidi-стрім між клієнтом і Codex CLI app-server.

Frame format (orjson-serialized JSON):
  client → server:
    {"type": "user_message", "text": "...", "chat_id": int|null,
     "attachment_ids": [int]}
    {"type": "interrupt"}
  server → client:
    {"type": "ready"}
    {"type": "chat", "chat_id": int}
    {"type": "token",  "delta": "..."}
    {"type": "tool_call", "name": "...", "args": {...}}
    {"type": "tool_result", "name": "...", "text": "...",
     "media": [{"kind": "image", "src": "...", "caption": "..."}],
     "error": null}
    {"type": "done", "chat_id": int, "final_text": "..."}
    {"type": "error", "code": "...", "detail": "..."}
"""

import asyncio
import contextlib

import orjson
import structlog
from fastapi import WebSocket, WebSocketDisconnect

from app.config import settings
from app.db.base import SessionLocal
from app.models import EventKind, MessageRole
from app.services.bus.default import event_bus
from app.services.codex.events import (
    Attachment,
    DoneEvent,
    ErrorEvent,
    TokenEvent,
    ToolCallEvent,
    ToolCallRecord,
    ToolResultEvent,
    event_to_frame,
    iterate_with_idle_timeout,
)
from app.services.events.default import event_service
from app.services.messages.default import message_service
from app.services.sessions.store import ChatSession
from app.services.uploads.default import upload_service
from app.services.users.default import user_service
from app.ws.sessions import web_sessions

log = structlog.get_logger(__name__)

# Single-user web setup: one synthetic User backs all browser sessions.
# Switch to per-OAuth-account users when multi-user lands.
_WEB_USER_EMAIL = "web@codex.local"


class ChatWebSocketHandler:
    """Один екземпляр на додаток. WS-конекти одного web user reuse Codex session."""

    def __init__(self) -> None:
        self._cached_web_user_pk: int | None = None
        self._user_pk_lock = asyncio.Lock()

    async def handle(self, websocket: WebSocket, jwt_subject: str) -> None:
        await websocket.accept()
        user_pk = await self._resolve_web_user_pk()
        try:
            session = await web_sessions.get_or_open(user_pk)
        except Exception as exc:  # noqa: BLE001 — будь-яка помилка → frame + close
            log.error("codex_connect_failed", subject=jwt_subject, error=str(exc))
            await self._safe_send(
                websocket,
                {"type": "error", "code": "codex_unavailable", "detail": str(exc)},
            )
            await websocket.close(code=1011)
            return

        try:
            await self._safe_send(websocket, {"type": "ready"})
            async for raw in websocket.iter_text():
                await self._dispatch(websocket, session, raw)
        except WebSocketDisconnect:
            log.info("ws_disconnected", subject=jwt_subject)

    async def _dispatch(
        self,
        ws: WebSocket,
        session: ChatSession,
        raw: str,
    ) -> None:
        try:
            frame = orjson.loads(raw)
        except orjson.JSONDecodeError:
            await self._safe_send(ws, {"type": "error", "code": "bad_json"})
            return

        kind = frame.get("type")
        if kind == "user_message":
            await self._handle_user_message(ws, session, frame)
        elif kind == "interrupt":
            await session.client.interrupt()
        else:
            await self._safe_send(
                ws,
                {"type": "error", "code": "bad_frame", "detail": f"unknown type: {kind}"},
            )

    async def _handle_user_message(
        self,
        ws: WebSocket,
        session: ChatSession,
        frame: dict,
    ) -> None:
        text: str = (frame.get("text") or "").strip()
        if not text:
            await self._safe_send(ws, {"type": "error", "code": "empty_text"})
            return

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

        await self._safe_send(ws, {"type": "chat", "chat_id": persisted_chat_id})

        async with session.turn_lock:
            await web_sessions.seed_history_if_fresh_thread(session)
            current = asyncio.current_task()
            session.current_turn_task = current
            try:
                await self._stream_turn(ws, session, text, persisted_chat_id, user_pk)
            finally:
                if session.current_turn_task is current:
                    session.current_turn_task = None

    async def _stream_turn(
        self,
        ws: WebSocket,
        session: ChatSession,
        text: str,
        persisted_chat_id: int,
        user_pk: int,
    ) -> None:
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
                "ws_codex_idle_timeout",
                db_chat_id=persisted_chat_id,
                idle_timeout_s=settings.WS_TURN_TIMEOUT_SECONDS,
                events_count=events_count,
                last_event_type=last_event_type,
            )
            with contextlib.suppress(Exception):
                await session.client.interrupt()

        try:
            async for ev in iterate_with_idle_timeout(
                stream,
                settings.WS_TURN_TIMEOUT_SECONDS,
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
                        await self._safe_send(ws, event_to_frame(ev))
                        await self._emit_event(
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
                await self._safe_send(ws, event_to_frame(ev))
        except WebSocketDisconnect:
            raise
        except TimeoutError:
            await self._safe_send(
                ws,
                {
                    "type": "error",
                    "code": "turn_timeout",
                    "detail": f"idle>{settings.WS_TURN_TIMEOUT_SECONDS}s",
                },
            )
            await self._emit_event(
                persisted_chat_id,
                user_pk,
                EventKind.TURN_FAILED,
                {"code": "turn_timeout", "detail": f"idle>{settings.WS_TURN_TIMEOUT_SECONDS}s"},
            )
            return
        except Exception as exc:  # noqa: BLE001
            log.error("codex_run_turn_failed", exc_type=type(exc).__name__, error=str(exc))
            await self._safe_send(
                ws,
                {"type": "error", "code": "codex_error", "detail": str(exc)},
            )
            await self._emit_event(
                persisted_chat_id, user_pk, EventKind.TURN_FAILED, {"exc_type": type(exc).__name__}
            )
            return

        if not done_seen:
            await self._safe_send(
                ws,
                {
                    "type": "error",
                    "code": "stream_dropped",
                    "detail": "Codex stream ended without completion",
                },
            )
            await self._emit_event(
                persisted_chat_id, user_pk, EventKind.TURN_FAILED, {"reason": "stream_dropped"}
            )
            return

        async with SessionLocal() as db:
            upload_ids = await upload_service.persist_attachments(
                db,
                persisted_chat_id,
                attachments,
            )
            meta: dict = {}
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

        await self._safe_send(
            ws,
            {
                "type": "done",
                "chat_id": persisted_chat_id,
                "final_text": _final_text_for_done_frame(final_text, streamed_text),
            },
        )

    async def _resolve_web_user_pk(self) -> int:
        if self._cached_web_user_pk is not None:
            return self._cached_web_user_pk
        async with self._user_pk_lock:
            if self._cached_web_user_pk is not None:
                return self._cached_web_user_pk
            async with SessionLocal() as session:
                user = await user_service.get_or_create_by_email(session, _WEB_USER_EMAIL)
                await session.commit()
                self._cached_web_user_pk = user.id
                return user.id

    async def _emit_event(
        self,
        chat_id: int,
        user_pk: int,
        kind: EventKind,
        payload: dict | None = None,
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

    @staticmethod
    async def _safe_send(ws: WebSocket, payload: dict) -> None:
        # Браузер очікує text-frame'и (window.WebSocket.onmessage event.data → str).
        with contextlib.suppress(WebSocketDisconnect, RuntimeError):
            await ws.send_text(orjson.dumps(payload).decode())


chat_ws_handler = ChatWebSocketHandler()


def _final_text_for_done_frame(final_text: str, streamed_text: str) -> str:
    """Dedup для frontend: якщо клієнт уже зібрав full text з token-deltas,
    шлемо порожній final_text у `done` frame щоб не показати дубль.

    Рідкий випадок (sidecar emit'ить весь agentMessage одним item'ом замість
    стрімінгу) — final_text != streamed_text → повний текст.
    """
    if streamed_text and final_text == streamed_text:
        return ""
    return final_text
