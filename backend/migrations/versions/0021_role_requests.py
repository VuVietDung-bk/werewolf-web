"""Player role requests with private event references.

Revision ID: 0021_role_requests
Revises: 0020_chat_mutes
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0021_role_requests"
down_revision = "0020_chat_mutes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "role_requests",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("room_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("game_events.id", ondelete="CASCADE"), unique=True, nullable=False),
        sa.Column("sequence", sa.BigInteger(), nullable=False),
        sa.Column("role_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("role_name", sa.String(80), nullable=False),
        sa.Column("phase_type", sa.String(16), nullable=False),
        sa.Column("round_number", sa.Integer(), nullable=False),
        sa.Column("text", sa.String(2000), nullable=False),
        sa.Column("status", sa.String(20), server_default="pending", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["room_id", "user_id"],
                                ["player_states.room_id", "player_states.user_id"], ondelete="CASCADE"),
    )
    op.create_index("ix_role_requests_room_sequence", "role_requests", ["room_id", "sequence"])
    op.create_index("ix_role_requests_room_user_created", "role_requests",
                    ["room_id", "user_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_role_requests_room_user_created", table_name="role_requests")
    op.drop_index("ix_role_requests_room_sequence", table_name="role_requests")
    op.drop_table("role_requests")
