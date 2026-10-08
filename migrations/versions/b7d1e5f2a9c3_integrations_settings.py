"""integrations settings

Revision ID: b7d1e5f2a9c3
Revises: d229700c948e
Create Date: 2026-10-08 12:00:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "b7d1e5f2a9c3"
down_revision: Union[str, Sequence[str], None] = "d229700c948e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _common() -> list[sa.Column]:
    return [
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
    ]


def upgrade() -> None:
    op.create_table(
        "integrations",
        *_common(),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("host", sa.String(length=253), nullable=False),
        sa.Column("username", sa.String(length=100), nullable=False),
        sa.Column("secret", sa.Text(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("options", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )
    op.create_table(
        "telegram_chats",
        *_common(),
        sa.Column("bot_id", sa.BigInteger(), nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("kind", sa.String(length=30), nullable=False),
        sa.Column("membership", sa.String(length=30), nullable=False),
        sa.Column("replies_enabled", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("bot_id", "chat_id", name="uq_telegram_bot_chat"),
    )
    op.add_column("users", sa.Column("tg_username", sa.String(length=64), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "tg_username")
    op.drop_table("telegram_chats")
    op.drop_table("integrations")
