"""Invite CRUD + single-use redemption.

Token format: 32-char url-safe random (`secrets.token_urlsafe(24)` дає 32 chars).
Активний invite — `used_at IS NULL AND expires_at > now()`.
"""

import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Invite

_TOKEN_BYTES = 24  # → 32-char base64url
_DEFAULT_TTL_DAYS = 7
_MAX_TTL_DAYS = 30


class InviteError(Exception):
    """Спільний базовий клас для всіх invite-фейлів."""


class InviteNotFound(InviteError):
    """Токен не існує."""


class InviteUsed(InviteError):
    """Токен уже redeem'нутий."""


class InviteExpired(InviteError):
    """Токен прострочений."""


class InviteService:
    async def create(
        self,
        session: AsyncSession,
        *,
        created_by_user_id: int,
        ttl_days: int = 0,
    ) -> Invite:
        days = ttl_days if 0 < ttl_days <= _MAX_TTL_DAYS else _DEFAULT_TTL_DAYS
        invite = Invite(
            token=secrets.token_urlsafe(_TOKEN_BYTES),
            created_by_user_id=created_by_user_id,
            expires_at=datetime.now(UTC) + timedelta(days=days),
        )
        session.add(invite)
        await session.flush()
        return invite

    async def get_by_token(
        self,
        session: AsyncSession,
        token: str,
    ) -> Invite | None:
        stmt = select(Invite).where(Invite.token == token)
        return (await session.execute(stmt)).scalar_one_or_none()

    async def redeem(
        self,
        session: AsyncSession,
        *,
        token: str,
        user_id: int,
    ) -> Invite:
        """Помічає invite як used. Атомарне: SELECT FOR UPDATE блокує row
        до commit/rollback — два concurrent redeem'и того ж token'у
        серіалізуються, другий бачить used_at != None і кидає InviteUsed.
        """
        stmt = select(Invite).where(Invite.token == token).with_for_update()
        invite = (await session.execute(stmt)).scalar_one_or_none()
        if invite is None:
            raise InviteNotFound(token)
        if invite.used_at is not None:
            raise InviteUsed(token)
        if invite.expires_at <= datetime.now(UTC):
            raise InviteExpired(token)
        invite.used_at = datetime.now(UTC)
        invite.used_by_user_id = user_id
        await session.flush()
        return invite

    async def list_active(self, session: AsyncSession) -> list[Invite]:
        stmt = (
            select(Invite)
            .where(Invite.used_at.is_(None), Invite.expires_at > datetime.now(UTC))
            .order_by(Invite.created_at.desc())
        )
        rows = await session.execute(stmt)
        return list(rows.scalars())

    async def list_all(self, session: AsyncSession) -> list[Invite]:
        stmt = select(Invite).order_by(Invite.created_at.desc())
        rows = await session.execute(stmt)
        return list(rows.scalars())

    async def revoke(self, session: AsyncSession, invite_id: int) -> bool:
        """Hard-delete активного запрошення. Used — лишаємо як audit-trail."""
        invite = await session.get(Invite, invite_id)
        if invite is None or invite.used_at is not None:
            return False
        await session.delete(invite)
        return True
