"""Durable, paginated room chat with recipient snapshots for team history.

Revision ID: 0017_chat_messages
Revises: 0016_vote_publications
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0017_chat_messages"
down_revision = "0016_vote_publications"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "chat_messages",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("room_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("rooms.id", ondelete="CASCADE"), nullable=False),
        sa.Column("event_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("game_events.id", ondelete="CASCADE"), unique=True, nullable=False),
        sa.Column("sequence", sa.BigInteger(), nullable=False),
        sa.Column("channel", sa.String(12), nullable=False),
        sa.Column("team_label", sa.String(80)),
        sa.Column("participant_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="RESTRICT")),
        sa.Column("sender_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("recipient_ids", postgresql.JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("text", sa.String(2000), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("channel IN ('common', 'team', 'host')", name="ck_chat_messages_channel"),
    )
    op.create_index("ix_chat_messages_room_channel_sequence", "chat_messages", ["room_id", "channel", "sequence"])
    op.create_index("ix_chat_messages_team_recipients", "chat_messages", ["recipient_ids"], postgresql_using="gin")


def downgrade() -> None:
    op.drop_index("ix_chat_messages_team_recipients", table_name="chat_messages")
    op.drop_index("ix_chat_messages_room_channel_sequence", table_name="chat_messages")
    op.drop_table("chat_messages")
