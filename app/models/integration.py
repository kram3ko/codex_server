from sqlalchemy import BigInteger, Boolean, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Integration(Base):
    __tablename__ = "integrations"

    name: Mapped[str] = mapped_column(String(100), unique=True)
    kind: Mapped[str] = mapped_column(String(20))
    host: Mapped[str] = mapped_column(String(253), default="")
    username: Mapped[str] = mapped_column(String(100), default="")
    secret: Mapped[str] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    options: Mapped[dict] = mapped_column(JSONB, default=dict)


class TelegramChat(Base):
    __tablename__ = "telegram_chats"
    __table_args__ = (UniqueConstraint("bot_id", "chat_id", name="uq_telegram_bot_chat"),)

    bot_id: Mapped[int] = mapped_column(BigInteger)
    chat_id: Mapped[int] = mapped_column(BigInteger)
    title: Mapped[str] = mapped_column(String(255))
    kind: Mapped[str] = mapped_column(String(30))
    membership: Mapped[str] = mapped_column(String(30), default="unknown")
    replies_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
