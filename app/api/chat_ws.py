"""WebSocket /chat/ws router."""

from typing import Annotated

from fastapi import APIRouter, Depends, WebSocket

from app.api.docs.chat_ws_docs import CHAT_WS_DESCRIPTION, CHAT_WS_SUMMARY
from app.deps.auth import require_user_ws
from app.ws.chat import chat_ws_handler

router = APIRouter(tags=["chat"])


@router.websocket("/chat/ws", name="chat_ws")
async def chat_websocket(
    websocket: WebSocket,
    user_id: Annotated[str, Depends(require_user_ws)],
) -> None:
    await chat_ws_handler.handle(websocket, user_id)


chat_websocket.__doc__ = f"{CHAT_WS_SUMMARY}\n\n{CHAT_WS_DESCRIPTION}"
