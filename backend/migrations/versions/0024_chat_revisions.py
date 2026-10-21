"""Track changes to old chat messages for cursor based synchronization.

Revision ID: 0024_chat_revisions
Revises: 0023_publication_disposition
"""

from alembic import op
import sqlalchemy as sa

revision = "0024_chat_revisions"
down_revision = "0023_publication_disposition"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("chat_messages", sa.Column("updated_sequence", sa.BigInteger(), nullable=True))
    op.execute("UPDATE chat_messages SET updated_sequence = sequence")
    op.alter_column("chat_messages", "updated_sequence", nullable=False)
    op.create_index("ix_chat_messages_room_updated_sequence", "chat_messages",
                    ["room_id", "updated_sequence"])


def downgrade() -> None:
    op.drop_index("ix_chat_messages_room_updated_sequence", table_name="chat_messages")
    op.drop_column("chat_messages", "updated_sequence")
