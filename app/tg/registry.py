from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware, Bot
from aiogram.types import ChatMemberUpdated, Message, TelegramObject
from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert

from app.db.base import SessionLocal
from app.models import TelegramChat, User


class ChatRegistry(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if not isinstance(event, (Message, ChatMemberUpdated)):
            return await handler(event, data)
        bot: Bot = data["bot"]
        chat = event.chat
        values = {
            "bot_id": bot.id,
            "chat_id": chat.id,
            "title": chat.title or chat.full_name,
            "kind": str(chat.type),
        }
        changes = {"title": values["title"], "kind": values["kind"], "updated_at": func.now()}
        if isinstance(event, ChatMemberUpdated):
            changes["membership"] = str(event.new_chat_member.status)
            values["membership"] = str(event.new_chat_member.status)
        async with SessionLocal() as db:
            await db.execute(
                insert(TelegramChat)
                .values(**values)
                .on_conflict_do_update(
                    constraint="uq_telegram_bot_chat",
                    set_=changes,
                )
            )
            sender = event.from_user
            if sender is not None:
                await db.execute(
                    update(User)
                    .where(User.tg_user_id == sender.id)
                    .values(
                        tg_username=sender.username,
                        display_name=sender.full_name,
                    )
                )
            enabled = await db.scalar(
                select(TelegramChat.replies_enabled).where(
                    TelegramChat.bot_id == bot.id,
                    TelegramChat.chat_id == chat.id,
                )
            )
            await db.commit()
        if isinstance(event, Message) and not enabled:
            return None
        result = await handler(event, data)
        if sender is not None:
            async with SessionLocal() as db:
                await db.execute(
                    update(User)
                    .where(User.tg_user_id == sender.id)
                    .values(tg_username=sender.username)
                )
                await db.commit()
        return result


async def membership_changed(event: ChatMemberUpdated) -> None:
    pass
