"""Stable channel identities, independent of current audience or display name.

Revision ID: 0029_room_channels
Revises: 0028_private_message_positions
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0029_room_channels"
down_revision = "0028_private_message_positions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table("room_channels",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("room_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("rooms.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String(24), nullable=False),
        sa.Column("channel_key", sa.String(80), nullable=False),
        sa.UniqueConstraint("room_id", "kind", "channel_key", name="uq_room_channels_identity"),
        sa.CheckConstraint("(kind IN ('common','announcement','vote') AND channel_key = '') OR "
                           "(kind = 'team' AND length(channel_key) > 0) OR "
                           "(kind = 'moderator_dm' AND channel_key ~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')",
                           name="ck_room_channels_key"))
    op.execute("""
        INSERT INTO room_channels(id, room_id, kind, channel_key)
        SELECT gen_random_uuid(), id, kind, '' FROM rooms
        CROSS JOIN (VALUES ('common'),('announcement'),('vote')) AS kinds(kind)
    """)
    op.execute("""
        INSERT INTO room_channels(id, room_id, kind, channel_key)
        SELECT gen_random_uuid(), room_id, 'moderator_dm', user_id::text FROM room_members
    """)
    op.execute("""
        INSERT INTO room_channels(id, room_id, kind, channel_key)
        SELECT gen_random_uuid(), room_id, 'team', label FROM (
            SELECT room_id, team_chat_label AS label FROM role_definitions
            UNION SELECT room_id, team_label FROM chat_messages WHERE channel = 'team'
            UNION SELECT room_id, team_chat_label_override FROM player_states WHERE team_chat_override_set
            UNION SELECT room_id, item->>'team_chat_label'
                FROM role_snapshots CROSS JOIN LATERAL jsonb_array_elements(definitions) AS item
        ) AS labels WHERE label IS NOT NULL AND label <> ''
    """)


def downgrade() -> None:
    op.drop_table("room_channels")
