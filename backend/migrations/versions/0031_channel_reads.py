"""Private monotonic read positions; historical backfill starts without new badges.

Revision ID: 0031_channel_reads
Revises: 0030_room_deliveries
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '0031_channel_reads'
down_revision = '0030_room_deliveries'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('channel_reads',
        sa.Column('room_id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('user_id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('channel_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('room_channels.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('position', sa.BigInteger(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(['room_id', 'user_id'], ['room_delivery_streams.room_id', 'room_delivery_streams.user_id'], ondelete='CASCADE'),
        sa.CheckConstraint('position >= 0', name='ck_channel_read_position'))
    # A new read model must not turn the delivery backfill into fresh unread chat.
    # Never reconstruct historical membership from today's team/role.
    op.execute("""
        WITH sources AS (
            SELECT event_id,
                CASE channel WHEN 'host' THEN 'moderator_dm' ELSE channel END AS kind,
                CASE channel WHEN 'common' THEN '' WHEN 'team' THEN team_label ELSE participant_user_id::text END AS channel_key
                FROM chat_messages
            UNION ALL SELECT event_id,'moderator_dm',user_id::text FROM private_messages
        ) INSERT INTO channel_reads(room_id,user_id,channel_id,position)
            SELECT d.room_id,d.user_id,ch.id,max(d.position)
            FROM room_deliveries d JOIN sources s ON s.event_id=d.source_event_id
            JOIN room_channels ch ON ch.room_id=d.room_id AND ch.kind=s.kind AND ch.channel_key=s.channel_key
            GROUP BY d.room_id,d.user_id,ch.id
    """)


def downgrade() -> None:
    op.drop_table('channel_reads')
