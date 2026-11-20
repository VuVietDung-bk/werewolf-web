"""Current Unicode reactions; history remains in the append-only event journal.

Revision ID: 0032_message_reactions
Revises: 0031_channel_reads
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '0032_message_reactions'
down_revision = '0031_channel_reads'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('message_reactions',
        sa.Column('source_event_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('game_events.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('user_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('users.id', ondelete='RESTRICT'), primary_key=True),
        sa.Column('emoji', sa.String(32), primary_key=True),
        sa.Column('room_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('rooms.id', ondelete='CASCADE'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()))
    op.create_index('ix_message_reactions_room_user', 'message_reactions', ['room_id', 'user_id'])


def downgrade() -> None:
    op.drop_table('message_reactions')
