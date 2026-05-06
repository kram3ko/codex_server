"""FastAPI entrypoint:
- Connect-RPC ASGI mount на /api  (AuthService, HealthService)
- WebSocket /chat/ws  — bidi-стрім чату
- HTTP /health        — службовий (docker healthcheck, без proto/RPC)
"""

import logging
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, WebSocket

from app.db.base import engine
from app.deps.auth import require_user_ws
from app.grpc_generated.codex.v1.auth_connect import AuthServiceASGIApplication
from app.grpc_generated.codex.v1.common_connect import HealthServiceASGIApplication
from app.rpc.auth import AuthRPC
from app.rpc.health import HealthRPC
from app.rpc.router import ConnectRouter
from app.services.auth_service import auth_service
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


# --- Connect-RPC services ---------------------------------------------------
connect_router = ConnectRouter(
    services=[
        AuthServiceASGIApplication(AuthRPC(auth_service)),
        HealthServiceASGIApplication(HealthRPC()),
    ]
)
app.mount("/api", connect_router)


# --- HTTP /health (для docker healthcheck) ----------------------------------
@app.get("/health", include_in_schema=False)
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "codex-server"}


# --- WebSocket /chat/ws ------------------------------------------------------
@app.websocket("/chat/ws")
async def chat_websocket(
    websocket: WebSocket,
    user_id: Annotated[str, Depends(require_user_ws)],
) -> None:
    await chat_ws_handler.handle(websocket, user_id)
