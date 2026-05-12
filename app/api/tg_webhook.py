"""Telegram webhook endpoint. Telegram POSTs updates here; secret-token guard."""

import secrets

import structlog
from aiogram.types import Update
from fastapi import APIRouter, Header, HTTPException, Request, status

from app.tg.service import tg_bot_service

log = structlog.get_logger(__name__)

router = APIRouter(tags=["telegram"])

_SECRET_HEADER = "X-Telegram-Bot-Api-Secret-Token"


@router.post("/tg/webhook", include_in_schema=False)
async def telegram_webhook(
    request: Request,
    x_telegram_bot_api_secret_token: str | None = Header(default=None, alias=_SECRET_HEADER),
) -> dict[str, bool]:
    expected = tg_bot_service.webhook_secret
    if not expected:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "webhook not configured")
    presented = x_telegram_bot_api_secret_token or ""
    if not secrets.compare_digest(presented, expected):
        log.warning("tg_webhook_secret_mismatch")
        raise HTTPException(status.HTTP_403_FORBIDDEN, "invalid secret token")
    body = await request.body()
    update = Update.model_validate_json(body)
    await tg_bot_service.feed_update(update)
    return {"ok": True}
