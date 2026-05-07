"""User CRUD. Tx boundary lives at the caller."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User


class UserService:
    async def get(self, session: AsyncSession, user_id: int) -> User | None:
        return await session.get(User, user_id)

    async def get_or_create_by_tg(
        self,
        session: AsyncSession,
        tg_user_id: int,
        display_name: str | None = None,
    ) -> User:
        existing = (
            await session.execute(select(User).where(User.tg_user_id == tg_user_id))
        ).scalar_one_or_none()
        if existing is not None:
            if display_name and existing.display_name != display_name:
                existing.display_name = display_name
                await session.flush()
            return existing
        user = User(tg_user_id=tg_user_id, display_name=display_name)
        session.add(user)
        await session.flush()
        return user

    async def get_or_create_by_email(
        self,
        session: AsyncSession,
        email: str,
        display_name: str | None = None,
    ) -> User:
        existing = (
            await session.execute(select(User).where(User.email == email))
        ).scalar_one_or_none()
        if existing is not None:
            return existing
        user = User(email=email, display_name=display_name)
        session.add(user)
        await session.flush()
        return user
