"""Persist chat-conversations + messages у Postgres.

Tx-boundary тримає caller (handler): сервіс лише робить SELECT/INSERT/UPDATE
у переданій сесії, не дзвонить commit. Так handler може батчити
"user_message + початок turn'у" у одну транзакцію, або тримати окремі
короткі транзакції — на свій смак.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import func

from app.models import Conversation, Message, MessageRole


class ConversationService:
    """Stateless. Singleton живе в `conversation_service` нижче."""

    async def get_or_create(
        self,
        session: AsyncSession,
        conv_id: int | None,
    ) -> Conversation:
        if conv_id is not None:
            existing = await session.get(Conversation, conv_id)
            if existing is not None:
                return existing
        conv = Conversation()
        session.add(conv)
        await session.flush()
        return conv

    async def append_user_message(
        self,
        session: AsyncSession,
        conv: Conversation,
        text: str,
    ) -> Message:
        message = Message(conv_id=conv.id, role=MessageRole.USER, text=text)
        session.add(message)
        conv.last_msg_at = func.now()
        session.add(conv)
        await session.flush()
        return message

    async def append_assistant_message(
        self,
        session: AsyncSession,
        conv: Conversation,
        text: str,
        tool_calls: list[dict] | None,
    ) -> Message:
        # `tool_calls` колонка JSONB має тип dict — обгортаємо list у
        # {"calls": [...]}; залишає простір для майбутніх ключів типу
        # {"reasoning": "..."} без alembic-міграції.
        payload: dict | None = {"calls": tool_calls} if tool_calls else None
        message = Message(
            conv_id=conv.id,
            role=MessageRole.ASSISTANT,
            text=text,
            tool_calls=payload,
        )
        session.add(message)
        conv.last_msg_at = func.now()
        session.add(conv)
        await session.flush()
        return message

    async def list_recent(
        self,
        session: AsyncSession,
        limit: int = 50,
    ) -> list[Conversation]:
        stmt = (
            select(Conversation)
            .order_by(Conversation.last_msg_at.desc())
            .limit(limit)
        )
        return list((await session.execute(stmt)).scalars())


conversation_service = ConversationService()
