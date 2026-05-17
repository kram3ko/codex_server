"""Durable turn lifecycle — single source of truth для one assistant exchange.

`turns_active_per_chat` partial unique fence-ить race на concurrent INSERT
(заміна Redis NX-lock з TTL-розіграшем). Assistant message — lazy bind
після першого token-а; `ON DELETE SET NULL` щоб видалення message не
каскадило turn.
"""

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Enum, ForeignKey, Index, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.enums import TurnStatus


class Turn(Base):
    __tablename__ = "turns"

    chat_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("chats.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_message_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("messages.id", ondelete="SET NULL"),
        nullable=False,
    )
    assistant_message_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("messages.id", ondelete="SET NULL"),
        nullable=True,
    )

    status: Mapped[TurnStatus] = mapped_column(
        Enum(TurnStatus, name="turn_status"),
        nullable=False,
        default=TurnStatus.STARTING,
    )
    codex_thread_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    codex_turn_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    sidecar: Mapped[str | None] = mapped_column(Text, nullable=True)

    stream_key: Mapped[str] = mapped_column(Text, nullable=False)
    error_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_event_id: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Окремий від `Base.updated_at` бо туди б'є кожен UPDATE — зашумило би liveness.
    heartbeat_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index(
            "turns_active_per_chat",
            "chat_id",
            unique=True,
            postgresql_where="status IN ('STARTING', 'RUNNING')",
        ),
        Index(
            "turns_stale_heartbeat",
            "heartbeat_at",
            postgresql_where="status = 'RUNNING'",
        ),
        Index("turns_by_chat", "chat_id", "created_at"),
        Index("turns_by_user", "user_id", "created_at"),
    )
