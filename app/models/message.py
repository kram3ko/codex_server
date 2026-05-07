"""Single user/assistant/tool message inside a Chat. Append-only."""

from typing import Any

from sqlalchemy import BigInteger, Enum, ForeignKey, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.enums import MessageRole


class Message(Base):
    __tablename__ = "messages"

    chat_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("chats.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    role: Mapped[MessageRole] = mapped_column(
        Enum(MessageRole, name="message_role"),
        nullable=False,
    )
    text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    meta: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
