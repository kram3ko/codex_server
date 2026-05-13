"""Append/list messages inside a Chat. Tx boundary lives at the caller."""

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Message, MessageRole
from app.services.chats.default import chat_service


class MessageService:
    async def append(
        self,
        session: AsyncSession,
        chat_id: int,
        role: MessageRole,
        text: str,
        meta: dict[str, Any] | None = None,
    ) -> Message:
        message = Message(chat_id=chat_id, role=role, text=text, meta=meta)
        session.add(message)
        await chat_service.touch_last_msg_at(session, chat_id)
        await session.flush()
        return message

    async def update_text(
        self,
        session: AsyncSession,
        message_id: int,
        text: str,
        meta: dict[str, Any] | None = None,
    ) -> None:
        msg = await session.get(Message, message_id)
        if msg is None:
            return
        msg.text = text
        msg.meta = meta  # always assign — allow clearing stale partial flag
        await session.flush()

    async def list(
        self,
        session: AsyncSession,
        chat_id: int,
        limit: int = 50,
        before_id: int | None = None,
    ) -> list[Message]:
        # Cursor-based infinite scroll: DESC query + reverse so caller always
        # gets ascending order. `before_id` стабільний до конкурентних insert'ів,
        # offset зрушиться, cursor — ні.
        stmt = select(Message).where(Message.chat_id == chat_id)
        if before_id is not None:
            stmt = stmt.where(Message.id < before_id)
        rows = await session.execute(
            stmt.order_by(Message.id.desc()).limit(limit),
        )
        return list(reversed(rows.scalars().all()))

    async def list_recent(
        self,
        session: AsyncSession,
        chat_id: int,
        limit: int = 50,
    ) -> list[Message]:
        rows = await session.execute(
            select(Message)
            .where(Message.chat_id == chat_id)
            .order_by(Message.id.desc())
            .limit(limit),
        )
        return list(reversed(rows.scalars().all()))
