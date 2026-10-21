"""Chat moderation state and sender rate lookup.

Revision ID: 0020_chat_mutes
Revises: 0019_chat_policy
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0020_chat_mutes"
down_revision = "0019_chat_policy"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index("ix_chat_messages_room_sender_created", "chat_messages",
                    ["room_id", "sender_user_id", "created_at"])
    op.create_table(
        "chat_mutes",
        sa.Column("room_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("is_muted", sa.Boolean(), nullable=False),
        sa.Column("reason", sa.String(length=1000), nullable=False),
        sa.Column("actor_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["room_id", "user_id"], ["room_members.room_id", "room_members.user_id"],
                                ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("room_id", "user_id"),
    )


def downgrade() -> None:
    op.drop_table("chat_mutes")
    op.drop_index("ix_chat_messages_room_sender_created", table_name="chat_messages")
