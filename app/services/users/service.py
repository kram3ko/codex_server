"""User CRUD + role bootstrap. Tx boundary lives at the caller."""

from typing import cast

from sqlalchemy import CursorResult, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import User, UserRole


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
        role = UserRole.ADMIN if tg_user_id in settings.TG_ADMIN_USER_IDS else UserRole.USER
        user = User(tg_user_id=tg_user_id, display_name=display_name, role=role)
        session.add(user)
        await session.flush()
        return user

    async def get_by_email(self, session: AsyncSession, email: str) -> User | None:
        return (await session.execute(select(User).where(User.email == email))).scalar_one_or_none()

    async def get_or_create_by_email(
        self,
        session: AsyncSession,
        email: str,
        display_name: str | None = None,
    ) -> User:
        """Concurrent-safe upsert. Якщо інший воркер уже вставив рядок —
        Postgres'ний `ON CONFLICT DO NOTHING` обходить race, потім беремо
        існуючий через SELECT."""
        stmt = (
            insert(User)
            .values(email=email, display_name=display_name)
            .on_conflict_do_nothing(index_elements=["email"])
            .returning(User)
        )
        inserted = (await session.execute(stmt)).scalar_one_or_none()
        if inserted is not None:
            return inserted
        return (await session.execute(select(User).where(User.email == email))).scalar_one()

    async def set_password_hash(
        self,
        session: AsyncSession,
        user: User,
        password_hash: str,
    ) -> None:
        user.password_hash = password_hash
        await session.flush()

    async def ensure_admin_roles(self, session: AsyncSession) -> int:
        """Idempotent UPDATE: для кожного `tg_user_id` з env що уже є у БД як
        USER → promote до ADMIN. Викликати на startup. Returns кількість
        promoted рядків (для логування)."""
        admins = settings.TG_ADMIN_USER_IDS
        if not admins:
            return 0
        result = await session.execute(
            update(User)
            .where(
                User.tg_user_id.in_(admins),
                User.role == UserRole.USER,
            )
            .values(role=UserRole.ADMIN),
        )
        return cast(CursorResult, result).rowcount or 0
