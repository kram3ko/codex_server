"""uploads.user_id NOT NULL + FK users

Revision ID: c7d4e8a9b2f1
Revises: b1f2c3d4e5a6
Create Date: 2026-05-12 12:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c7d4e8a9b2f1"
down_revision: Union[str, Sequence[str], None] = "b1f2c3d4e5a6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("DELETE FROM uploads WHERE chat_id IS NULL")
    op.add_column("uploads", sa.Column("user_id", sa.BigInteger(), nullable=True))
    op.execute("UPDATE uploads SET user_id = c.user_id FROM chats c WHERE uploads.chat_id = c.id")
    op.execute("DELETE FROM uploads WHERE user_id IS NULL")
    op.alter_column("uploads", "user_id", nullable=False)
    op.create_foreign_key(
        "fk_uploads_user_id_users",
        "uploads",
        "users",
        ["user_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index("ix_uploads_user_id", "uploads", ["user_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_uploads_user_id", table_name="uploads")
    op.drop_constraint("fk_uploads_user_id_users", "uploads", type_="foreignkey")
    op.drop_column("uploads", "user_id")
