"""Atomic turn lifecycle transitions. Tx boundary lives at the caller."""

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import cast

from sqlalchemy import CursorResult, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Turn, TurnStatus
from app.services.turns.schemas import TurnCreate, TurnRow


def _stream_key(turn_id: int) -> str:
    return f"turn:{turn_id}:events"


def _now() -> datetime:
    return datetime.now(UTC)


class TurnService:
    async def try_create_starting(
        self,
        session: AsyncSession,
        payload: TurnCreate,
    ) -> TurnRow | None:
        """Race-safe create: повертає None якщо partial-unique fence спрацював
        (інший active turn у тому ж chat). Caller-у пропонує retry-сценарій
        без catch-у DB-exception на handler-рівні."""
        try:
            return await self.create_starting(session, payload)
        except IntegrityError:
            await session.rollback()
            return None

    async def create_starting(
        self,
        session: AsyncSession,
        payload: TurnCreate,
    ) -> TurnRow:
        """Race з другим INSERT → `IntegrityError` (partial unique fence).
        Caller повинен ловити (або краще — кликати `try_create_starting`)."""
        now = _now()
        turn = Turn(
            chat_id=payload.chat_id,
            user_id=payload.user_id,
            user_message_id=payload.user_message_id,
            assistant_message_id=None,
            status=TurnStatus.STARTING,
            codex_thread_id=None,
            codex_turn_id=None,
            sidecar=payload.sidecar,
            stream_key="",  # placeholder, нижче перепишеться
            error_code=None,
            error_detail=None,
            last_event_id=None,
            heartbeat_at=now,
            completed_at=None,
        )
        session.add(turn)
        await session.flush()
        turn.stream_key = _stream_key(turn.id)
        await session.flush()
        # Refresh-имо щоб витягти `created_at`/`updated_at` із server_default —
        # без цього `model_validate` ловить `MissingGreenlet` на lazy access.
        await session.refresh(turn)
        return TurnRow.model_validate(turn)

    async def mark_running(
        self,
        session: AsyncSession,
        turn_id: int,
        codex_thread_id: str,
        codex_turn_id: str,
    ) -> bool:
        """CAS STARTING → RUNNING. False якщо уже terminal або ownership lost."""
        now = _now()
        stmt = (
            update(Turn)
            .where(Turn.id == turn_id, Turn.status == TurnStatus.STARTING)
            .values(
                status=TurnStatus.RUNNING,
                codex_thread_id=codex_thread_id,
                codex_turn_id=codex_turn_id,
                heartbeat_at=now,
            )
        )
        result = cast(CursorResult, await session.execute(stmt))
        return bool(result.rowcount)

    async def attach_assistant_message(
        self,
        session: AsyncSession,
        turn_id: int,
        message_id: int,
    ) -> None:
        await session.execute(
            update(Turn).where(Turn.id == turn_id).values(assistant_message_id=message_id)
        )

    async def update_last_event(
        self,
        session: AsyncSession,
        turn_id: int,
        event_id: str,
    ) -> None:
        await session.execute(update(Turn).where(Turn.id == turn_id).values(last_event_id=event_id))

    async def heartbeat(self, session: AsyncSession, turn_id: int) -> bool:
        """False = ownership lost; caller MUST stop і finalize FAILED.
        Match STARTING + RUNNING — pre-handshake фаза теж потребує heartbeat-у
        (mark_running переключає на RUNNING лише після `turn/start`)."""
        stmt = (
            update(Turn)
            .where(
                Turn.id == turn_id,
                Turn.status.in_((TurnStatus.STARTING, TurnStatus.RUNNING)),
            )
            .values(heartbeat_at=_now())
        )
        result = cast(CursorResult, await session.execute(stmt))
        return bool(result.rowcount)

    async def finalize_once(
        self,
        session: AsyncSession,
        turn_id: int,
        status: TurnStatus,
        *,
        error_code: str | None = None,
        error_detail: str | None = None,
    ) -> bool:
        """Exactly-once: тільки перший concurrent caller transition-ить."""
        if status not in (
            TurnStatus.COMPLETED,
            TurnStatus.FAILED,
            TurnStatus.CANCELLED,
        ):
            raise ValueError(f"finalize_once expects terminal status, got {status}")
        now = _now()
        stmt = (
            update(Turn)
            .where(
                Turn.id == turn_id,
                Turn.status.in_((TurnStatus.STARTING, TurnStatus.RUNNING)),
            )
            .values(
                status=status,
                error_code=error_code,
                error_detail=error_detail,
                completed_at=now,
            )
        )
        result = cast(CursorResult, await session.execute(stmt))
        return bool(result.rowcount)

    async def get_active_for_chat(
        self,
        session: AsyncSession,
        chat_id: int,
    ) -> TurnRow | None:
        stmt = (
            select(Turn)
            .where(
                Turn.chat_id == chat_id,
                Turn.status.in_((TurnStatus.STARTING, TurnStatus.RUNNING)),
            )
            .limit(1)
        )
        row = await session.execute(stmt)
        turn = row.scalar_one_or_none()
        return TurnRow.model_validate(turn) if turn is not None else None

    async def get_by_id(
        self,
        session: AsyncSession,
        turn_id: int,
    ) -> TurnRow | None:
        turn = await session.get(Turn, turn_id)
        return TurnRow.model_validate(turn) if turn is not None else None

    async def find_stale_active(
        self,
        session: AsyncSession,
        threshold: timedelta,
    ) -> Sequence[TurnRow]:
        """STARTING orphan-и (lock contention, enqueue failure) + RUNNING
        orphan-и (worker crash). Recovery finalize-ить обидва як FAILED."""
        cutoff = _now() - threshold
        stmt = select(Turn).where(
            Turn.status.in_((TurnStatus.STARTING, TurnStatus.RUNNING)),
            Turn.heartbeat_at < cutoff,
        )
        rows = await session.execute(stmt)
        return [TurnRow.model_validate(t) for t in rows.scalars().all()]
