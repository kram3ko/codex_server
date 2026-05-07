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
from app.services.chats.default import chat_service
from app.services.codex.client import CodexClient
from app.services.codex.events import (
    Attachment,
    DoneEvent,
    ErrorEvent,
    ToolCallEvent,
    ToolResultEvent,
    event_to_frame,
)
from app.services.events.default import event_service
from app.services.messages.default import message_service
from app.services.uploads.default import upload_service
from app.services.users.default import user_service

log = structlog.get_logger(__name__)

# Single-user web setup: one synthetic User backs all browser sessions.
# Switch to per-OAuth-account users when multi-user lands.
_WEB_USER_EMAIL = "web@codex.local"


class ChatWebSocketHandler:
    """Один екземпляр на додаток. Кожен WS-конект отримує власний CodexClient."""

    def __init__(self) -> None:
        self._cached_web_user_pk: int | None = None
        self._user_pk_lock = asyncio.Lock()

    async def handle(self, websocket: WebSocket, jwt_subject: str) -> None:
        await websocket.accept()
        user_pk = await self._resolve_web_user_pk()
        codex = CodexClient(
            url=settings.CODEX_APP_SERVER_URL,
            cwd=settings.CODEX_CWD,
            approval_policy=settings.CODEX_APPROVAL_POLICY,
            sandbox=settings.CODEX_SANDBOX,
            request_timeout=settings.CODEX_REQUEST_TIMEOUT_SECONDS,
            reasoning_effort=settings.CODEX_REASONING_EFFORT,
        )
        try:
            await codex.connect()
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
                await self._dispatch(websocket, codex, user_pk, raw)
        except WebSocketDisconnect:
            log.info("ws_disconnected", subject=jwt_subject)
        finally:
            await codex.close()

    async def _dispatch(
        self,
        ws: WebSocket,
        codex: CodexClient,
        user_pk: int,
        raw: str,
    ) -> None:
        try:
            frame = orjson.loads(raw)
        except orjson.JSONDecodeError:
            await self._safe_send(ws, {"type": "error", "code": "bad_json"})
            return

        kind = frame.get("type")
        if kind == "user_message":
            await self._handle_user_message(ws, codex, user_pk, frame)
        elif kind == "interrupt":
            await codex.interrupt()
        else:
            await self._safe_send(
                ws,
                {"type": "error", "code": "bad_frame", "detail": f"unknown type: {kind}"},
            )

    async def _handle_user_message(
        self,
        ws: WebSocket,
        codex: CodexClient,
        user_pk: int,
        frame: dict,
    ) -> None:
        text: str = (frame.get("text") or "").strip()
        if not text:
            await self._safe_send(ws, {"type": "error", "code": "empty_text"})
            return

        chat_id_raw = frame.get("chat_id")
        chat_id = chat_id_raw if isinstance(chat_id_raw, int) else None

        async with SessionLocal() as session:
            chat = (
                await chat_service.get(session, chat_id)
                if chat_id is not None
                else None
            )
            if chat is None:
                chat = await chat_service.create_web_chat(session, user_pk)
            await message_service.append(session, chat.id, MessageRole.USER, text)
            await event_service.emit(
                session,
                EventKind.TURN_STARTED,
                chat_id=chat.id,
                user_id=user_pk,
                payload={"text_len": len(text)},
            )
            await session.commit()
            persisted_chat_id = chat.id

        await self._safe_send(ws, {"type": "chat", "chat_id": persisted_chat_id})

        final_text = ""
        tool_calls: list[dict] = []
        attachments: list[Attachment] = []
        done_seen = False
        try:
            async for ev in codex.run_turn(text):
                await event_bus.publish(persisted_chat_id, ev)
                match ev:
                    case ToolCallEvent(name=name, args=args):
                        tool_calls.append({"name": name, "args": args})
                    case ToolResultEvent(attachments=tool_files):
                        attachments.extend(tool_files)
                    case ErrorEvent(code=code, detail=detail):
                        await self._safe_send(ws, event_to_frame(ev))
                        await self._emit_event(
                            persisted_chat_id, user_pk, EventKind.TURN_FAILED,
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
        except Exception as exc:  # noqa: BLE001
            log.error("codex_run_turn_failed", exc_type=type(exc).__name__, error=str(exc))
            await self._safe_send(
                ws, {"type": "error", "code": "codex_error", "detail": str(exc)},
            )
            await self._emit_event(persisted_chat_id, user_pk, EventKind.TURN_FAILED,
                                   {"exc_type": type(exc).__name__})
            return

        if not done_seen:
            await self._safe_send(
                ws,
                {"type": "error", "code": "stream_dropped",
                 "detail": "Codex stream ended without completion"},
            )
            await self._emit_event(persisted_chat_id, user_pk, EventKind.TURN_FAILED,
                                   {"reason": "stream_dropped"})
            return

        async with SessionLocal() as session:
            upload_ids = await upload_service.persist_attachments(
                session, persisted_chat_id, attachments,
            )
            meta: dict = {}
            if tool_calls:
                meta["calls"] = tool_calls
            if upload_ids:
                meta["upload_ids"] = upload_ids
            await message_service.append(
                session,
                persisted_chat_id,
                MessageRole.ASSISTANT,
                final_text,
                meta=meta or None,
            )
            await event_service.emit(
                session,
                EventKind.TURN_COMPLETED,
                chat_id=persisted_chat_id,
                user_id=user_pk,
                payload={
                    "final_text_len": len(final_text),
                    "tool_calls": len(tool_calls),
                    "uploads": len(upload_ids),
                },
            )
            await session.commit()

        await self._safe_send(
            ws,
            {"type": "done", "chat_id": persisted_chat_id, "final_text": final_text},
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
                session, kind, chat_id=chat_id, user_id=user_pk, payload=payload,
            )
            await session.commit()

    @staticmethod
    async def _safe_send(ws: WebSocket, payload: dict) -> None:
        # Браузер очікує text-frame'и (window.WebSocket.onmessage event.data → str).
        with contextlib.suppress(WebSocketDisconnect, RuntimeError):
            await ws.send_text(orjson.dumps(payload).decode())


chat_ws_handler = ChatWebSocketHandler()
