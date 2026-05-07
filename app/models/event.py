"""Append-only event log — observability + (eventual) outbox."""

from typing import Any

from sqlalchemy import BigInteger, Enum, ForeignKey
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.enums import EventKind


class Event(Base):
    __tablename__ = "events"

    chat_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("chats.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    user_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    kind: Mapped[EventKind] = mapped_column(
        Enum(EventKind, name="event_kind"),
        nullable=False,
        index=True,
    )
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
