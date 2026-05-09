"""Notes/knowledge base entries — full-text search via tsvector."""

from sqlalchemy import Computed, Index, String, Text
from sqlalchemy.dialects.postgresql import ARRAY, TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Note(Base):
    __tablename__ = "notes"

    title: Mapped[str] = mapped_column(String(255), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    tags: Mapped[list[str]] = mapped_column(
        ARRAY(String),
        nullable=False,
        default=list,
        server_default="{}",
    )
    search_vector: Mapped[str] = mapped_column(
        TSVECTOR,
        Computed("to_tsvector('simple', title || ' ' || body)", persisted=True),
        nullable=False,
    )

    __table_args__ = (
        Index("ix_notes_search_vector", "search_vector", postgresql_using="gin"),
        Index("ix_notes_tags", "tags", postgresql_using="gin"),
    )
