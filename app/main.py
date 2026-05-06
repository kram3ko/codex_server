"""FastAPI entrypoint:
- Connect-RPC ASGI app mount на /api  (TODO: після генерації protobuf stubs)
- WebSocket /chat/ws  — bidi-стрім чату
- HTTP /health        — службовий (docker healthcheck)
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, WebSocket

from app.config import settings
from app.db.base import engine
from app.deps.auth import require_user_ws
from app.ws.chat import chat_ws_handler

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("app_startup")
    # TODO(T5): start Codex CLI client connection
    # TODO(T12): start Telegram bot polling
    yield
    logger.info("app_shutdown")
    await engine.dispose()


app = FastAPI(
    title="Codex Server",
    version="0.1.0",
    lifespan=lifespan,
)


@app.get("/health", include_in_schema=False)
async def health() -> dict[str, str]:
    """Технічний healthcheck для docker — без auth."""
    return {"status": "ok", "service": "codex-server"}


@app.websocket("/chat/ws")
async def chat_websocket(
    websocket: WebSocket,
    user_id: Annotated[str, Depends(require_user_ws)],
) -> None:
    await chat_ws_handler.handle(websocket, user_id)


# TODO: після `./scripts/gen-proto.sh` mount Connect ASGI apps:
#   from app.grpc_generated.codex.v1.auth_connect import AuthServiceASGIApplication
#   from app.rpc.auth import AuthRPC
#   app.mount("/api", AuthServiceASGIApplication(AuthRPC()))
_ = settings  # silence "imported but unused" until rpc handlers wire it
