"""User CRUD + role bootstrap. Tx boundary lives at the caller."""

from typing import cast

from sqlalchemy import CursorResult, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import Turn, User, UserRole
from app.services.users.schemas import UserProfile


class UserService:
    async def get(self, session: AsyncSession, user_id: int) -> User | None:
        return await session.get(User, user_id)

    async def list_all(self, session: AsyncSession) -> list[User]:
        rows = await session.execute(select(User).order_by(User.id))
        return list(rows.scalars())

    async def list_profiles(self, session: AsyncSession) -> list[UserProfile]:
        last_turn = (
            select(Turn.user_id, func.max(Turn.created_at).label("last_active_at"))
            .group_by(Turn.user_id)
            .subquery()
        )
        rows = await session.execute(
            select(User.id, User.tg_username, last_turn.c.last_active_at)
            .outerjoin(last_turn, last_turn.c.user_id == User.id)
            .order_by(User.id)
        )
        return [
            UserProfile(id=str(user_id), tg_username=tg_username, last_active_at=last_active_at)
            for user_id, tg_username, last_active_at in rows
        ]

    async def get_or_create_by_tg(
        self,
        session: AsyncSession,
        tg_user_id: int,
        display_name: str | None = None,
    ) -> User:
        existing = (
            await session.execute(select(User).where(User.tg_user_id == tg_user_id))
        ).scalar_one_or_none()
        if existing is None:
            role = UserRole.ADMIN if tg_user_id in settings.TG_ADMIN_USER_IDS else UserRole.USER
            user = User(tg_user_id=tg_user_id, display_name=display_name, role=role)
            session.add(user)
            await session.flush()
            return user
        if display_name and existing.display_name != display_name:
            existing.display_name = display_name
            await session.flush()
        return existing

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

    async def sync_admin_roles(self, session: AsyncSession, admin_ids: set[int]) -> tuple[int, int]:
        """Idempotent: TG-юзери зі списку → ADMIN, TG-only ADMIN поза списком →
        USER. Web-акаунти (з email) не чіпаємо — їхня роль живе окремо.
        Returns (promoted, demoted)."""
        promoted = 0
        if admin_ids:
            result = await session.execute(
                update(User)
                .where(User.tg_user_id.in_(admin_ids), User.role == UserRole.USER)
                .values(role=UserRole.ADMIN),
            )
            promoted = cast(CursorResult, result).rowcount or 0
        demote = update(User).where(
            User.tg_user_id.is_not(None),
            User.email.is_(None),
            User.role == UserRole.ADMIN,
        )
        if admin_ids:
            demote = demote.where(User.tg_user_id.not_in(admin_ids))
        result = await session.execute(demote.values(role=UserRole.USER))
        return promoted, cast(CursorResult, result).rowcount or 0
