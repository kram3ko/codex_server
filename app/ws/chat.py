"""WebSocket /chat/ws — bidi-stream між клієнтом і Codex CLI app-server.

Frame format (JSON):
  client → server:
    {"type": "user_message", "text": "...", "conv_id": int|null,
     "attachment_ids": [int]}
    {"type": "interrupt"}
  server → client:
    {"type": "token",  "delta": "..."}
    {"type": "tool_call", "name": "...", "args": {...}}
    {"type": "done",   "conv_id": int}
    {"type": "error",  "message": "..."}

Поки що це skeleton — конкретний обмін з Codex client'ом імплементую
у наступному проході (T5/T6). Зараз endpoint просто echo+close.
"""

from __future__ import annotations

import json
import logging

from fastapi import WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)


class ChatWebSocketHandler:
    """Один екземпляр на додаток — приймає підключення і керує турним."""

    async def handle(self, websocket: WebSocket, user_id: str) -> None:
        await websocket.accept()
        logger.info("ws_connected", extra={"user": user_id})
        try:
            await websocket.send_json({"type": "ready"})
            async for raw in websocket.iter_text():
                frame = self._parse_frame(raw)
                await self._dispatch(websocket, frame)
        except WebSocketDisconnect:
            logger.info("ws_disconnected", extra={"user": user_id})

    def _parse_frame(self, raw: str) -> dict:
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {"type": "invalid"}

    async def _dispatch(self, websocket: WebSocket, frame: dict) -> None:
        kind = frame.get("type")
        if kind == "user_message":
            # TODO(T5/T6): передати у CodexClient.run_turn(), стрімити токени
            await websocket.send_json(
                {"type": "token", "delta": f"echo: {frame.get('text', '')}"}
            )
            await websocket.send_json({"type": "done", "conv_id": 0})
        elif kind == "interrupt":
            # TODO(T5): codex_client.interrupt(turn_id)
            await websocket.send_json({"type": "ready"})
        else:
            await websocket.send_json(
                {"type": "error", "message": f"unknown frame type: {kind}"}
            )


chat_ws_handler = ChatWebSocketHandler()
