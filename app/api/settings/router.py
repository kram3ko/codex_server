from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select

from app.db.base import SessionLocal
from app.models import Integration, TelegramChat, User, UserRole
from app.services.auth.cookie import read_jwt
from app.services.auth.default import auth_service
from app.services.auth.service import InvalidToken
from app.services.integrations.default import integration_service
from app.services.integrations.schemas import ChatUpdate, IntegrationInput, IntegrationView
from app.services.runtime_settings.default import runtime_settings_service
from app.services.runtime_settings.schemas import RuntimeSettings
from app.services.users.default import user_service
from app.services.users.schemas import UserProfile


async def admin_session(request: Request) -> User:
    if request.method != "GET" and request.headers.get("x-settings-request") != "1":
        raise HTTPException(403, "Missing request verification header")
    token = read_jwt(request.headers.get("cookie"))
    if token is None:
        raise HTTPException(401, "Sign in required")
    try:
        email = auth_service.validate_token(token)
    except InvalidToken as exc:
        raise HTTPException(401, "Session expired") from exc
    async with SessionLocal() as db:
        user = await user_service.get_by_email(db, email)
    if user is None or user.role != UserRole.ADMIN:
        raise HTTPException(403, "Administrator access required")
    return user


router = APIRouter(prefix="/api/settings", dependencies=[Depends(admin_session)])


@router.get("/integrations", response_model=list[IntegrationView])
async def list_integrations():
    async with SessionLocal() as db:
        return [integration_service.view(row) for row in await integration_service.list(db)]


async def save(data: IntegrationInput, entry_id: int | None):
    try:
        async with SessionLocal() as db:
            row = await integration_service.save(db, data, entry_id)
            await db.commit()
            return integration_service.view(row)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/integrations", response_model=IntegrationView)
async def create_integration(data: IntegrationInput):
    return await save(data, None)


@router.put("/integrations/{entry_id}", response_model=IntegrationView)
async def update_integration(entry_id: int, data: IntegrationInput):
    return await save(data, entry_id)


@router.delete("/integrations/{entry_id}")
async def delete_integration(entry_id: int):
    async with SessionLocal() as db:
        row = await db.get(Integration, entry_id)
        if row is None:
            raise HTTPException(404, "Integration not found")
        await db.delete(row)
        await db.commit()
    return {"ok": True}


@router.post("/integrations/{entry_id}/check")
async def check_integration(entry_id: int):
    async with SessionLocal() as db:
        row = await db.get(Integration, entry_id)
        if row is None:
            raise HTTPException(404, "Integration not found")
        try:
            return {"message": await integration_service.check(row)}
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc


@router.get("/telegram/chats")
async def telegram_chats():
    async with SessionLocal() as db:
        rows = await db.scalars(select(TelegramChat).order_by(TelegramChat.updated_at.desc()))
        return [
            {
                "id": row.id,
                "bot_id": str(row.bot_id),
                "chat_id": str(row.chat_id),
                "title": row.title,
                "kind": row.kind,
                "membership": row.membership,
                "replies_enabled": row.replies_enabled,
                "updated_at": row.updated_at,
            }
            for row in rows
        ]


@router.get("/telegram/status")
async def telegram_status():
    from app.tg.service import tg_bot_service

    bot = tg_bot_service.bot
    return {"mode": tg_bot_service.mode, "bot_id": str(bot.id) if bot else None}


@router.patch("/telegram/chats/{entry_id}")
async def update_chat(entry_id: int, data: ChatUpdate):
    async with SessionLocal() as db:
        row = await db.get(TelegramChat, entry_id)
        if row is None:
            raise HTTPException(404, "Chat not found")
        row.replies_enabled = data.replies_enabled
        await db.commit()
    return {"ok": True}


@router.get("/users", response_model=list[UserProfile])
async def users():
    async with SessionLocal() as db:
        return await user_service.list_profiles(db)


@router.get("/limits", response_model=RuntimeSettings)
async def limits():
    async with SessionLocal() as db:
        return await runtime_settings_service.load(db)


@router.put("/limits", response_model=RuntimeSettings)
async def update_limits(data: RuntimeSettings):
    async with SessionLocal() as db:
        saved = await runtime_settings_service.save(db, data)
        await db.commit()
    runtime_settings_service.remember(saved)
    return saved
