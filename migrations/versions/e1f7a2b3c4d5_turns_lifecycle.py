"""turns lifecycle table

Revision ID: e1f7a2b3c4d5
Revises: d8e5f3a4b6c2
Create Date: 2026-05-17 10:00:00.000000

Durable turn lifecycle як single source of truth. Замінює розмазування стану
між `turn_registry` (Redis), `messages.meta.partial` і `events` table.

Partial unique index `turns_active_per_chat` — race-free pre-lock замість
NX-Redis-set з TTL-розіграшем. `turns_stale_heartbeat` — efficient lookup
для startup recovery / periodic cleaner-а orphan-task-ів.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "e1f7a2b3c4d5"
down_revision: Union[str, Sequence[str], None] = "d8e5f3a4b6c2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # `create_type=False` на column-rederences — інакше `op.create_table`
    # повторно auto-CREATE-ує ENUM і падає `DuplicateObjectError`.
    turn_status = postgresql.ENUM(
        "STARTING",
        "RUNNING",
        "COMPLETED",
        "FAILED",
        "CANCELLED",
        name="turn_status",
        create_type=False,
    )
    postgresql.ENUM(
        "STARTING",
        "RUNNING",
        "COMPLETED",
        "FAILED",
        "CANCELLED",
        name="turn_status",
    ).create(op.get_bind(), checkfirst=True)

    op.create_table(
        "turns",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "chat_id",
            sa.BigInteger(),
            sa.ForeignKey("chats.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            sa.BigInteger(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_message_id",
            sa.BigInteger(),
            sa.ForeignKey("messages.id", ondelete="SET NULL"),
            nullable=False,
        ),
        sa.Column(
            "assistant_message_id",
            sa.BigInteger(),
            sa.ForeignKey("messages.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("status", turn_status, nullable=False),
        sa.Column("codex_thread_id", sa.Text(), nullable=True),
        sa.Column("codex_turn_id", sa.Text(), nullable=True),
        sa.Column("sidecar", sa.Text(), nullable=True),
        sa.Column("stream_key", sa.Text(), nullable=False),
        sa.Column("error_code", sa.Text(), nullable=True),
        sa.Column("error_detail", sa.Text(), nullable=True),
        sa.Column("last_event_id", sa.Text(), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
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

    # Partial unique — фізична гарантія "1 active turn на chat". `INSERT ON
    # CONFLICT DO NOTHING` повертає 0 rows → caller знає що loser і шле BUSY.
    op.create_index(
        "turns_active_per_chat",
        "turns",
        ["chat_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('STARTING', 'RUNNING')"),
    )
    # Partial для cleaner-а: тільки живі turn-и, focused index без full-scan.
    op.create_index(
        "turns_stale_heartbeat",
        "turns",
        ["heartbeat_at"],
        postgresql_where=sa.text("status = 'RUNNING'"),
    )
    op.create_index("turns_by_chat", "turns", ["chat_id", "created_at"])
    op.create_index("turns_by_user", "turns", ["user_id", "created_at"])


def downgrade() -> None:
    op.drop_index("turns_by_user", table_name="turns")
    op.drop_index("turns_by_chat", table_name="turns")
    op.drop_index("turns_stale_heartbeat", table_name="turns")
    op.drop_index("turns_active_per_chat", table_name="turns")
    op.drop_table("turns")
    sa.Enum(name="turn_status").drop(op.get_bind(), checkfirst=True)
