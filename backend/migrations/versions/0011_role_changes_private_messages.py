"""Mutable current role/team and durable one-to-one moderator messages.

Revision ID: 0011_role_private
Revises: 0010_public_announcements
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0011_role_private"
down_revision = "0010_public_announcements"
branch_labels = None
depends_on = None


def upgrade() -> None:
    uuid = postgresql.UUID(as_uuid=True)
    op.add_column("player_states", sa.Column("current_role_id", uuid))
    op.add_column("player_states", sa.Column("faction_label_override", sa.String(80)))
    op.add_column("player_states", sa.Column("team_chat_override_set", sa.Boolean(),
                                              nullable=False, server_default=sa.false()))
    op.add_column("player_states", sa.Column("team_chat_label_override", sa.String(80)))
    op.create_table(
        "private_messages",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("room_id", uuid, nullable=False),
        sa.Column("user_id", uuid, nullable=False),
        sa.Column("sender_user_id", uuid, sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("event_id", uuid, sa.ForeignKey("game_events.id", ondelete="CASCADE"),
                  nullable=False, unique=True),
        sa.Column("text", sa.String(2000), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["room_id", "user_id"],
                                ["player_states.room_id", "player_states.user_id"],
                                ondelete="CASCADE"),
    )
    op.create_index("ix_private_messages_room_user_created", "private_messages",
                    ["room_id", "user_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_private_messages_room_user_created", table_name="private_messages")
    op.drop_table("private_messages")
    op.drop_column("player_states", "team_chat_label_override")
    op.drop_column("player_states", "team_chat_override_set")
    op.drop_column("player_states", "faction_label_override")
    op.drop_column("player_states", "current_role_id")
