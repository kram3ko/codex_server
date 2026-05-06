"""Append-only event log writer."""

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Event, EventKind


class EventService:
    async def emit(
        self,
        session: AsyncSession,
        kind: EventKind,
        chat_id: int | None = None,
        user_id: int | None = None,
        payload: dict[str, Any] | None = None,
    ) -> Event:
        event = Event(kind=kind, chat_id=chat_id, user_id=user_id, payload=payload)
        session.add(event)
        await session.flush()
        return event
