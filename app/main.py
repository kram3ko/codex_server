"""FastAPI entrypoint:
- Connect-RPC ASGI mount на /api  (AuthService, HealthService)
- WebSocket /chat/ws  — bidi-стрім чату
- HTTP /health        — службовий (docker healthcheck, без proto/RPC)

HTTP/WS routers живуть в `app/api/`; Connect-RPC services тримаються тут
(тонкий mount). Singletons (cache, tg_bot) у `lifespan` — старт + cleanup.
"""

from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI

from app.api.chat_ws import router as chat_ws_router
from app.api.health import router as health_router
from app.db.base import engine
from app.grpc_generated.codex.v1.auth_connect import AuthServiceASGIApplication
from app.grpc_generated.codex.v1.chat_connect import ChatServiceASGIApplication
from app.grpc_generated.codex.v1.common_connect import HealthServiceASGIApplication
from app.grpc_generated.codex.v1.event_connect import EventServiceASGIApplication
from app.grpc_generated.codex.v1.message_connect import MessageServiceASGIApplication
from app.grpc_generated.codex.v1.user_connect import UserServiceASGIApplication
from app.rpc.auth import AuthRPC
from app.rpc.chat import ChatRPC
from app.rpc.event import EventRPC
from app.rpc.health import HealthRPC
from app.rpc.message import MessageRPC
from app.rpc.router import ConnectRouter
from app.rpc.user import UserRPC
from app.services.auth.default import auth_service
from app.services.cache.default import cache
from app.tg.service import tg_bot_service

log = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    log.info("app_startup")
    await tg_bot_service.start()
    yield
    log.info("app_shutdown")
    await tg_bot_service.stop()
    await cache.aclose()
    await engine.dispose()


app = FastAPI(
    title="Codex API",
    version="0.1.0",
    lifespan=lifespan,
)

connect_router = ConnectRouter(
    services=[
        AuthServiceASGIApplication(AuthRPC(auth_service)),
        HealthServiceASGIApplication(HealthRPC()),
        UserServiceASGIApplication(UserRPC()),
        ChatServiceASGIApplication(ChatRPC()),
        MessageServiceASGIApplication(MessageRPC()),
        EventServiceASGIApplication(EventRPC()),
    ]
)
app.mount("/api", connect_router)  # type: ignore[arg-type]

app.include_router(health_router)
app.include_router(chat_ws_router)
