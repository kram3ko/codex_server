"""Notebook-owned notes with per-user scoping and Postgres full-text search."""

from sqlalchemy import (
    BigInteger,
    Computed,
    Enum,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ARRAY, TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.enums import ENUM_NAMES, NotebookKind


class Notebook(Base):
    __tablename__ = "notebooks"
    __table_args__ = (UniqueConstraint("user_id", "kind", name="uq_notebooks_user_kind"),)

    user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    kind: Mapped[NotebookKind] = mapped_column(
        Enum(NotebookKind, name=ENUM_NAMES[NotebookKind]),
        nullable=False,
        default=NotebookKind.PERSONAL,
        server_default=NotebookKind.PERSONAL.value,
    )


class Note(Base):
    __tablename__ = "notes"

    user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    notebook_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("notebooks.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
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
