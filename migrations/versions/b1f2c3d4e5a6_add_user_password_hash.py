"""add users.password_hash

Revision ID: b1f2c3d4e5a6
Revises: 8a3e1c5d4f02
Create Date: 2026-05-11 10:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b1f2c3d4e5a6"
down_revision: Union[str, Sequence[str], None] = "8a3e1c5d4f02"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("password_hash", sa.String(length=255), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("users", "password_hash")
