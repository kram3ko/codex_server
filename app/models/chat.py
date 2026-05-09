"""Chat = persistent conversation thread (web or TG) for one user.

Codex thread state живе тільки в RAM на стороні sidecar — ми його НЕ
персистимо, бо persistence давала б ілюзію rehydration що ламається на
кожен рестарт sidecar. Якщо потрібно справжнє відновлення контексту —
replay messages-таблиці в новий thread (TODO).
"""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    Enum,
    ForeignKey,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.enums import ChatSource


class Chat(Base):
    __tablename__ = "chats"
    __table_args__ = (UniqueConstraint("source", "tg_chat_id", name="uq_chats_source_tg_chat_id"),)

    user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    source: Mapped[ChatSource] = mapped_column(
        Enum(ChatSource, name="chat_source"),
        nullable=False,
    )
    tg_chat_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # Codex CLI sidecar тримає thread state in-memory. Зберігаємо id для
    # token-економії поки sidecar живе; на restart валідація йде через
    # optimistic retry: -32600 thread not found → invalidate + open new.
    codex_thread_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    last_msg_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
