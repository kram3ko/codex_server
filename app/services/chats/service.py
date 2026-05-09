"""Chat CRUD + Codex thread persistence."""

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import func

from app.models import Chat, ChatSource


class ChatService:
    async def get(self, session: AsyncSession, chat_id: int) -> Chat | None:
        return await session.get(Chat, chat_id)

    async def get_or_create_for_tg(
        self,
        session: AsyncSession,
        user_id: int,
        tg_chat_id: int,
    ) -> Chat:
        existing = (
            await session.execute(
                select(Chat).where(
                    Chat.source == ChatSource.TELEGRAM,
                    Chat.tg_chat_id == tg_chat_id,
                ),
            )
        ).scalar_one_or_none()
        if existing is not None:
            return existing
        chat = Chat(user_id=user_id, source=ChatSource.TELEGRAM, tg_chat_id=tg_chat_id)
        session.add(chat)
        await session.flush()
        return chat

    async def create_web_chat(self, session: AsyncSession, user_id: int) -> Chat:
        chat = Chat(user_id=user_id, source=ChatSource.WEB)
        session.add(chat)
        await session.flush()
        return chat

    async def get_or_create_for_web(self, session: AsyncSession, user_id: int) -> Chat:
        existing = (
            await session.execute(
                select(Chat)
                .where(
                    Chat.source == ChatSource.WEB,
                    Chat.user_id == user_id,
                )
                .order_by(Chat.id.asc())
                .limit(1)
            )
        ).scalars().first()
        if existing is not None:
            return existing
        return await self.create_web_chat(session, user_id)

    async def set_codex_thread_id(
        self,
        session: AsyncSession,
        chat_id: int,
        thread_id: str | None,
    ) -> None:
        await session.execute(
            update(Chat).where(Chat.id == chat_id).values(codex_thread_id=thread_id),
        )

    async def touch_last_msg_at(self, session: AsyncSession, chat_id: int) -> None:
        await session.execute(
            update(Chat).where(Chat.id == chat_id).values(last_msg_at=func.now()),
        )

    async def list_for_user(
        self,
        session: AsyncSession,
        user_id: int,
        limit: int = 50,
    ) -> list[Chat]:
        rows = await session.execute(
            select(Chat)
            .where(Chat.user_id == user_id)
            .order_by(Chat.last_msg_at.desc())
            .limit(limit),
        )
        return list(rows.scalars())
