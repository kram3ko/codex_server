"""FastAPI entrypoint:
- Connect-RPC ASGI mount на /api  (AuthService, HealthService)
- HTTP /health        — службовий (docker healthcheck, без proto/RPC)

HTTP/WS routers живуть в `app/api/`; Connect-RPC services тримаються тут
(тонкий mount). Singletons (cache, tg_bot) у `lifespan` — старт + cleanup.
"""

from contextlib import asynccontextmanager

import sentry_sdk
import structlog
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from sentry_sdk.integrations.asyncio import AsyncioIntegration
from sentry_sdk.integrations.fastapi import FastApiIntegration
from sentry_sdk.integrations.redis import RedisIntegration
from sentry_sdk.integrations.sqlalchemy import SqlalchemyIntegration
from sentry_sdk.integrations.starlette import StarletteIntegration

from app.api.health import router as health_router
from app.api.tg_webhook import router as tg_webhook_router
from app.config import settings
from app.services.errors.scrub import scrub_event

# Init at import time so boot-time errors (alembic, lifespan) are captured.
# Empty DSN → SDK no-op, zero overhead.
if settings.SENTRY_DSN:
    sentry_sdk.init(
        dsn=settings.SENTRY_DSN,
        release=settings.SENTRY_RELEASE or None,
        traces_sample_rate=settings.SENTRY_TRACES_SAMPLE_RATE,
        send_default_pii=False,
        send_client_reports=False,
        auto_session_tracking=False,
        before_send=scrub_event,
        integrations=[
            StarletteIntegration(),
            FastApiIntegration(),
            AsyncioIntegration(),
            SqlalchemyIntegration(),
            RedisIntegration(),
        ],
    )
from app.db.base import SessionLocal, engine
from app.grpc_generated.codex.v1.admin_connect import AdminServiceASGIApplication
from app.grpc_generated.codex.v1.auth_connect import AuthServiceASGIApplication
from app.grpc_generated.codex.v1.chat_connect import ChatServiceASGIApplication
from app.grpc_generated.codex.v1.common_connect import HealthServiceASGIApplication
from app.grpc_generated.codex.v1.event_connect import EventServiceASGIApplication
from app.grpc_generated.codex.v1.message_connect import MessageServiceASGIApplication
from app.grpc_generated.codex.v1.notes_connect import NotesServiceASGIApplication
from app.grpc_generated.codex.v1.uploads_connect import UploadsServiceASGIApplication
from app.grpc_generated.codex.v1.user_connect import UserServiceASGIApplication
from app.mcp import mcp_http_app
from app.models import UserRole
from app.rpc.admin import AdminRPC
from app.rpc.auth import AuthRPC
from app.rpc.chat import ChatRPC
from app.rpc.event import EventRPC
from app.rpc.health import HealthRPC
from app.rpc.message import MessageRPC
from app.rpc.notes import NotesRPC
from app.rpc.router import ConnectRouter
from app.rpc.uploads import UploadsRPC
from app.rpc.user import UserRPC
from app.services.auth.default import auth_service
from app.services.cache.default import cache
from app.services.errors.default import bugsink_client
from app.services.users.default import user_service
from app.tg.service import tg_bot_service

log = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # FastMCP lifespan стартує StreamableHTTPSessionManager — обов'язково wrap.
    async with mcp_http_app.lifespan(_app):
        log.info("app_startup")
        await _bootstrap_users()
        await tg_bot_service.start()
        yield
        log.info("app_shutdown")
        cancelled = await tg_bot_service.interrupt_active_turns()
        if cancelled:
            log.info("app_shutdown_turns_interrupted", count=cancelled)
        await tg_bot_service.stop()
        await bugsink_client.aclose()
        await cache.aclose()
        await engine.dispose()


async def _bootstrap_users() -> None:
    # Sync admin role + переписати hash якщо .env ADMIN_PASSWORD змінився.
    async with SessionLocal() as db:
        promoted = await user_service.ensure_admin_roles(db)
        await _sync_admin_account(db)
        await db.commit()
        if promoted:
            log.info("user_roles_admin_promoted", count=promoted)


async def _sync_admin_account(db) -> None:
    email = settings.ADMIN_EMAIL.strip().lower()
    if not email or not settings.ADMIN_PASSWORD:
        return
    admin = await user_service.get_or_create_by_email(db, email)
    if not await auth_service.verify_password(settings.ADMIN_PASSWORD, admin.password_hash):
        await user_service.set_password_hash(
            db, admin, await auth_service.hash_password(settings.ADMIN_PASSWORD)
        )
        log.info("admin_password_synced", email=admin.email)
    if admin.role != UserRole.ADMIN:
        admin.role = UserRole.ADMIN


app = FastAPI(
    title="Codex API",
    version="0.1.0",
    lifespan=lifespan,
)

connect_router = ConnectRouter(
    services=[
        AuthServiceASGIApplication(AuthRPC()),
        AdminServiceASGIApplication(AdminRPC()),
        HealthServiceASGIApplication(HealthRPC()),
        UserServiceASGIApplication(UserRPC()),
        ChatServiceASGIApplication(ChatRPC()),
        MessageServiceASGIApplication(MessageRPC()),
        EventServiceASGIApplication(EventRPC()),
        NotesServiceASGIApplication(NotesRPC()),
        UploadsServiceASGIApplication(UploadsRPC()),
    ]
)
app.mount("/api", connect_router)

# Codex image_generation outputs — read-only mount від sidecar'а; web
# рендерить картинки одразу під час streaming'у, без чекання S3 persist.
app.mount(
    "/generated",
    StaticFiles(directory="/home/codex/.codex/generated_images"),
    name="generated",
)

# FastMCP streamable-HTTP — URL: /mcp/streamable.
app.mount("/mcp", mcp_http_app)

app.include_router(health_router)
app.include_router(tg_webhook_router)
