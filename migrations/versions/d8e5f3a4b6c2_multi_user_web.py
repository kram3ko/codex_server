"""multi-user web: notes.user_id + invites table

Revision ID: d8e5f3a4b6c2
Revises: c7d4e8a9b2f1
Create Date: 2026-05-16 16:00:00.000000

Дві логічно пов'язані зміни одною міграцією — обидві потрібні для відкриття
веб-доступу друзям (notes.user_id ізолює нотатки per-owner, invites контролює
доступ до signup). Розкочується атомарно.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d8e5f3a4b6c2"
down_revision: Union[str, Sequence[str], None] = "c7d4e8a9b2f1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- notes.user_id ----------------------------------------------------
    # ADD COLUMN NOT NULL без DEFAULT — Postgres дозволяє на порожній таблиці;
    # на непорожній відмовиться сам (немає "правильного" owner'а для legacy
    # рядків, тому fail loud > arbitrary assign).
    op.add_column("notes", sa.Column("user_id", sa.BigInteger(), nullable=False))
    op.create_foreign_key(
        "fk_notes_user_id_users",
        "notes",
        "users",
        ["user_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index("ix_notes_user_id", "notes", ["user_id"], unique=False)

    # --- invites table ----------------------------------------------------
    op.create_table(
        "invites",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("token", sa.String(64), nullable=False, unique=True),
        sa.Column(
            "created_by_user_id",
            sa.BigInteger(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "used_by_user_id",
            sa.BigInteger(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_invites_token", "invites", ["token"], unique=True)
    op.create_index(
        "ix_invites_active",
        "invites",
        ["expires_at"],
        postgresql_where=sa.text("used_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_invites_active", table_name="invites")
    op.drop_index("ix_invites_token", table_name="invites")
    op.drop_table("invites")
    op.drop_index("ix_notes_user_id", table_name="notes")
    op.drop_constraint("fk_notes_user_id_users", "notes", type_="foreignkey")
    op.drop_column("notes", "user_id")
