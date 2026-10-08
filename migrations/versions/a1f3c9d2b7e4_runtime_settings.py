"""runtime_settings

Revision ID: a1f3c9d2b7e4
Revises: b7d1e5f2a9c3
Create Date: 2026-10-08 12:00:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "a1f3c9d2b7e4"
down_revision: Union[str, Sequence[str], None] = "b7d1e5f2a9c3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "runtime_settings",
        sa.Column("web_user_active", sa.Integer(), server_default="2", nullable=False),
        sa.Column("web_user_hourly", sa.Integer(), server_default="0", nullable=False),
        sa.Column("tg_guest_active", sa.Integer(), server_default="3", nullable=False),
        sa.Column("tg_guest_hourly", sa.Integer(), server_default="0", nullable=False),
        sa.Column("tg_guest_max_upload_mb", sa.Integer(), server_default="20", nullable=False),
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("id = 1", name="runtime_settings_singleton"),
        sa.CheckConstraint("web_user_active >= 0", name="runtime_settings_web_user_active"),
        sa.CheckConstraint("web_user_hourly >= 0", name="runtime_settings_web_user_hourly"),
        sa.CheckConstraint("tg_guest_active >= 0", name="runtime_settings_tg_guest_active"),
        sa.CheckConstraint("tg_guest_hourly >= 0", name="runtime_settings_tg_guest_hourly"),
        sa.CheckConstraint(
            "tg_guest_max_upload_mb BETWEEN 0 AND 20",
            name="runtime_settings_tg_guest_max_upload_mb",
        ),
    )
    op.execute("INSERT INTO runtime_settings (id) VALUES (1)")


def downgrade() -> None:
    op.drop_table("runtime_settings")
