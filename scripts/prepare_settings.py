"""Idempotent one-shot import of env tokens into encrypted integrations.

Schema comes from `alembic upgrade head`; this only seeds key + rows."""

import asyncio
import os
from pathlib import Path

from pydantic import SecretStr
from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert

from app.config import settings
from app.db.base import SessionLocal, engine
from app.models import Chat, ChatSource, Integration, TelegramChat
from app.services.integrations.default import integration_service
from app.services.integrations.schemas import IntegrationInput, IntegrationKind, TelegramOptions


def initialize_key(has_secrets: bool) -> None:
    if has_secrets and not Path(settings.INTEGRATIONS_KEY_FILE).exists():
        raise RuntimeError("Existing secrets require the original master key; restore it first")
    integration_service.cipher.ensure_key()


async def main() -> None:
    async with SessionLocal() as db:
        await db.execute(text("SELECT pg_advisory_xact_lock(6241831)"))
        count = await db.scalar(select(func.count()).select_from(Integration))
        await asyncio.to_thread(initialize_key, bool(count))
        existing = await integration_service.list(db)
        definitions = [
            (IntegrationKind.GITHUB, "GitHub", "github.com", os.environ.get("GH_TOKEN", "")),
            (IntegrationKind.GITLAB, "GitLab", "gitlab.com", os.environ.get("GITLAB_TOKEN", "")),
            (IntegrationKind.TELEGRAM, "Telegram", "", settings.TG_BOT_TOKEN),
        ]
        for kind, name, host, value in definitions:
            if not value or any(row.kind == kind for row in existing):
                continue
            await integration_service.save(
                db,
                IntegrationInput(
                    name=name,
                    kind=kind,
                    host=host,
                    secret=SecretStr(value),
                    telegram=TelegramOptions(
                        admin_ids=sorted(settings.TG_ADMIN_USER_IDS),
                        webhook_url=settings.TG_WEBHOOK_URL,
                    ),
                ),
                None,
            )
        await db.commit()
        telegram = await db.scalar(select(Integration).where(Integration.kind == "telegram"))
        if telegram is not None:
            from aiogram import Bot

            token = await asyncio.to_thread(integration_service.cipher.unseal, telegram.secret)
            bot = Bot(token)
            try:
                known_chats = await db.scalars(
                    select(Chat).where(Chat.source == ChatSource.TELEGRAM)
                )
                for chat in known_chats:
                    if chat.tg_chat_id is None:
                        continue
                    await db.execute(
                        insert(TelegramChat)
                        .values(
                            bot_id=bot.id,
                            chat_id=chat.tg_chat_id,
                            title=chat.title or str(chat.tg_chat_id),
                            kind="private" if chat.tg_chat_id > 0 else "group",
                            membership="unknown",
                            replies_enabled=True,
                        )
                        .on_conflict_do_nothing(constraint="uq_telegram_bot_chat")
                    )
                await db.commit()
            finally:
                await bot.session.close()
    await engine.dispose()
    print("Settings schema and encrypted integrations prepared. No secret values printed.")


if __name__ == "__main__":
    asyncio.run(main())
