"""Indexed positions for one timeline containing both chat message sources.

Revision ID: 0028_private_message_positions
Revises: 0027_vote_countdown
"""
from alembic import op
import sqlalchemy as sa

revision = "0028_private_message_positions"
down_revision = "0027_vote_countdown"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("private_messages", sa.Column("sequence", sa.BigInteger(), nullable=True))
    op.add_column("private_messages", sa.Column("updated_sequence", sa.BigInteger(), nullable=True))
    # Keep source IDs and timestamps. Old revisions must appear in changes, too.
    op.execute("""
        UPDATE private_messages AS message
        SET sequence = original.sequence,
            updated_sequence = GREATEST(original.sequence, COALESCE(changes.last_sequence, original.sequence))
        FROM game_events AS original LEFT JOIN (
            SELECT room_id, payload->'request'->>'source_event_id' AS source_id,
                   MAX(sequence) AS last_sequence
            FROM game_events WHERE kind = 'content.revision_decided'
            GROUP BY room_id, payload->'request'->>'source_event_id'
        ) AS changes ON changes.room_id = original.room_id AND changes.source_id = original.id::text
        WHERE original.id = message.event_id
    """)
    op.alter_column("private_messages", "sequence", nullable=False)
    op.alter_column("private_messages", "updated_sequence", nullable=False)
    op.create_index("ix_private_messages_room_user_sequence", "private_messages", ["room_id", "user_id", "sequence"])
    op.create_index("ix_private_messages_room_updated", "private_messages", ["room_id", "updated_sequence"])


def downgrade() -> None:
    op.drop_index("ix_private_messages_room_updated", table_name="private_messages")
    op.drop_index("ix_private_messages_room_user_sequence", table_name="private_messages")
    op.drop_column("private_messages", "updated_sequence")
    op.drop_column("private_messages", "sequence")
