"""Index moderator team and one-to-one chat history lookups.

Revision ID: 0018_chat_lookup_indexes
Revises: 0017_chat_messages
"""

from alembic import op

revision = "0018_chat_lookup_indexes"
down_revision = "0017_chat_messages"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index("ix_chat_messages_room_team_sequence", "chat_messages",
                    ["room_id", "team_label", "sequence"])
    op.create_index("ix_chat_messages_room_participant_sequence", "chat_messages",
                    ["room_id", "participant_user_id", "sequence"])


def downgrade() -> None:
    op.drop_index("ix_chat_messages_room_participant_sequence", table_name="chat_messages")
    op.drop_index("ix_chat_messages_room_team_sequence", table_name="chat_messages")
