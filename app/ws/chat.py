"""WebSocket /chat/ws — bidi-стрім між клієнтом і Codex CLI app-server.

Frame format (JSON):
  client → server:
    {"type": "user_message", "text": "...", "conv_id": int|null,
     "attachment_ids": [int]}
    {"type": "interrupt"}
  server → client:
    {"type": "ready"}
    {"type": "conv", "conv_id": int}
    {"type": "token",  "delta": "..."}
    {"type": "tool_call", "name": "...", "args": {...}}
    {"type": "tool_result", "name": "...", "result": "...", "error": null}
    {"type": "done", "conv_id": int, "final_text": "..."}
    {"type": "error", "code": "...", "detail": "..."}
"""

import json
import logging

from fastapi import WebSocket, WebSocketDisconnect

from app.config import settings
from app.db.base import SessionLocal
from app.models import Conversation
from app.services.codex import (
    CodexClient,
    DoneEvent,
    ErrorEvent,
    TokenEvent,
    ToolCallEvent,
    event_to_frame,
)
from app.services.conversation_service import (
    ConversationService,
    conversation_service,
)

logger = logging.getLogger(__name__)


class ChatWebSocketHandler:
    """Один екземпляр на додаток. Кожен WS-конект отримує власний CodexClient."""

    def __init__(self, conv_svc: ConversationService) -> None:
        self._conv_svc = conv_svc

    async def handle(self, websocket: WebSocket, user_id: str) -> None:
        await websocket.accept()
        codex = CodexClient(
            url=settings.CODEX_APP_SERVER_URL,
            cwd=settings.CODEX_CWD,
            approval_policy=settings.CODEX_APPROVAL_POLICY,
            sandbox=settings.CODEX_SANDBOX,
        )
        try:
            await codex.connect()
        except Exception as exc:  # noqa: BLE001 — будь-яка помилка → frame + close
            logger.error("codex_connect_failed user=%s error=%s", user_id, exc)
            await self._safe_send(websocket, {"type": "error", "code": "codex_unavailable", "detail": str(exc)})
            await websocket.close(code=1011)
            return

        try:
            await websocket.send_json({"type": "ready"})
            async for raw in websocket.iter_text():
                await self._dispatch(websocket, codex, raw)
        except WebSocketDisconnect:
            logger.info("ws_disconnected user=%s", user_id)
        finally:
            await codex.close()

    async def _dispatch(self, ws: WebSocket, codex: CodexClient, raw: str) -> None:
        try:
            frame = json.loads(raw)
        except json.JSONDecodeError:
            await self._safe_send(ws, {"type": "error", "code": "bad_json"})
            return

        kind = frame.get("type")
        if kind == "user_message":
            await self._handle_user_message(ws, codex, frame)
        elif kind == "interrupt":
            await codex.interrupt()
        else:
            await self._safe_send(ws, {"type": "error", "code": "bad_frame", "detail": f"unknown type: {kind}"})

    async def _handle_user_message(
        self,
        ws: WebSocket,
        codex: CodexClient,
        frame: dict,
    ) -> None:
        text: str = (frame.get("text") or "").strip()
        if not text:
            await self._safe_send(ws, {"type": "error", "code": "empty_text"})
            return

        conv_id_raw = frame.get("conv_id")
        conv_id = int(conv_id_raw) if isinstance(conv_id_raw, int) else None

        attachment_ids = frame.get("attachment_ids") or []
        if attachment_ids:
            # TODO(phase D): resolve uploads → presigned URLs → передати у CodexClient.run_turn
            logger.warning("attachments_ignored count=%d (uploads service not yet wired)", len(attachment_ids))

        # Persist user message + obtain conv id.
        async with SessionLocal() as session:
            conv = await self._conv_svc.get_or_create(session, conv_id)
            await self._conv_svc.append_user_message(session, conv, text)
            await session.commit()
            persisted_conv_id = conv.id

        await self._safe_send(ws, {"type": "conv", "conv_id": persisted_conv_id})

        # Stream Codex events. Accumulation робить CodexClient — handler
        # тільки збирає tool_calls для persist + бере final_text з DoneEvent.
        final_text = ""
        tool_calls: list[dict] = []
        try:
            async for ev in codex.run_turn(text):
                if isinstance(ev, ToolCallEvent):
                    tool_calls.append({"name": ev.name, "args": ev.args})
                if isinstance(ev, ErrorEvent):
                    await self._safe_send(ws, event_to_frame(ev))
                    return
                if isinstance(ev, DoneEvent):
                    final_text = ev.final_text
                    break
                # token / tool_call / tool_result — стрімимо у клієнт
                await self._safe_send(ws, event_to_frame(ev))
        except WebSocketDisconnect:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.error("codex_run_turn_failed exc_type=%s error=%s", type(exc).__name__, exc)
            await self._safe_send(ws, {"type": "error", "code": "codex_error", "detail": str(exc)})
            return

        # Persist assistant message.
        async with SessionLocal() as session:
            conv = await session.get(Conversation, persisted_conv_id)
            await self._conv_svc.append_assistant_message(
                session, conv, final_text, tool_calls or None
            )
            await session.commit()

        await self._safe_send(
            ws,
            {"type": "done", "conv_id": persisted_conv_id, "final_text": final_text},
        )

    @staticmethod
    async def _safe_send(ws: WebSocket, payload: dict) -> None:
        try:
            await ws.send_json(payload)
        except (WebSocketDisconnect, RuntimeError):
            # клієнт відключився — пропускаємо, finally закриє codex
            pass


chat_ws_handler = ChatWebSocketHandler(conversation_service)
