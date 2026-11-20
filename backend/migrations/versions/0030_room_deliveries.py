"""Recipient-scoped invalidations and private replay positions.

Revision ID: 0030_room_deliveries
Revises: 0029_room_channels
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '0030_room_deliveries'
down_revision = '0029_room_channels'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('room_delivery_streams',
        sa.Column('room_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('rooms.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('user_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('users.id', ondelete='RESTRICT'), primary_key=True),
        sa.Column('position', sa.BigInteger(), nullable=False, server_default='0'),
        sa.Column('policy_epoch', sa.BigInteger(), nullable=False, server_default='1'),
        sa.CheckConstraint('position >= 0 AND policy_epoch >= 1', name='ck_room_delivery_stream_position'))
    op.create_table('room_deliveries',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('room_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('user_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('position', sa.BigInteger(), nullable=False),
        sa.Column('source_event_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('game_events.id', ondelete='CASCADE'), nullable=False),
        sa.Column('scopes', postgresql.JSONB(), nullable=False),
        sa.Column('channel_ids', postgresql.JSONB(), nullable=False),
        sa.ForeignKeyConstraint(['room_id', 'user_id'], ['room_delivery_streams.room_id', 'room_delivery_streams.user_id'], ondelete='CASCADE'),
        sa.UniqueConstraint('room_id', 'user_id', 'position', name='uq_room_delivery_position'),
        sa.UniqueConstraint('room_id', 'user_id', 'source_event_id', name='uq_room_delivery_source'),
        sa.CheckConstraint('position > 0', name='ck_room_delivery_position'))
    # Frozen SQL, independent of future application audience changes. Public history
    # uses only the provable latest membership interval. Team recipients come from
    # stored send-time snapshots, never current role/team. Missing metadata denies.
    op.execute("""
        CREATE TEMP TABLE ux3_delivery_backfill ON COMMIT DROP AS
        WITH candidates AS (
            SELECT e.*, r.owner_user_id AS user_id FROM game_events e JOIN rooms r ON r.id=e.room_id
            UNION ALL
            SELECT e.*, m.user_id FROM game_events e
            JOIN room_members m ON m.room_id=e.room_id
            JOIN rooms r ON r.id=e.room_id
            WHERE m.user_id<>r.owner_user_id AND m.consent_version='postgame-full-v1'
                AND e.created_at>=m.joined_at AND (m.left_at IS NULL OR e.created_at<=m.left_at)
                AND CASE
                    WHEN e.kind IN ('chat.sent','chat.revised') THEN CASE e.payload->>'channel'
                        WHEN 'common' THEN true
                        WHEN 'team' THEN COALESCE(e.payload->'recipient_ids','[]'::jsonb) ? m.user_id::text
                        WHEN 'host' THEN e.payload->>'participant_user_id'=m.user_id::text
                        ELSE false END
                    WHEN e.kind='player.private_message_sent' THEN e.payload->>'recipient_user_id'=m.user_id::text
                    WHEN e.kind IN ('role.request_submitted','role.request_reviewed','role.request_withdrawn',
                        'player.kill','player.revive','player.add_effect','player.remove_effect',
                        'player.change_role','player.change_faction','player.change_team','chat.mute_changed')
                        THEN e.payload->'request'->>'user_id'=m.user_id::text
                    WHEN e.kind='vote.ballot_submitted' THEN e.actor_user_id=m.user_id
                    WHEN e.kind IN ('roles.draft_chosen','roles.draft_offered') THEN e.payload->>'user_id'=m.user_id::text
                    WHEN e.kind='roles.distributed' THEN EXISTS (
                        SELECT 1 FROM jsonb_array_elements(COALESCE(e.payload->'assignments','[]'::jsonb)) AS a
                        WHERE a->>'user_id'=m.user_id::text)
                    WHEN e.kind='roles.draft_started' THEN true
                    ELSE e.visibility='room' END
        ), scoped AS (
            SELECT c.*, row_number() OVER (PARTITION BY room_id,user_id ORDER BY sequence) AS position,
                CASE WHEN kind IN ('chat.sent','chat.revised','player.private_message_sent') THEN '["channels","chat"]'
                    WHEN kind IN ('role.request_submitted','role.request_reviewed','role.request_withdrawn') THEN '["channels","role_requests"]'
                    WHEN kind='chat.mute_changed' THEN '["channels"]'
                    WHEN kind IN ('player.kill','player.revive','player.add_effect','player.remove_effect',
                        'player.change_role','player.change_faction','player.change_team') OR kind LIKE 'roles.%%'
                        THEN '["channels","roles","room"]'
                    WHEN kind LIKE 'vote.%%' THEN CASE WHEN visibility='room' THEN '["announcements","vote"]' ELSE '["vote"]' END
                    WHEN kind IN ('content.notice_edited','content.notice_deleted','player.death_announced',
                        'player.revival_announced','announcement.published') THEN '["announcements","room"]'
                    WHEN visibility='room' THEN '["announcements","channels","roles","room","vote"]'
                    ELSE '["management"]' END::jsonb AS scopes,
                CASE WHEN kind IN ('chat.sent','chat.revised') THEN CASE payload->>'channel'
                        WHEN 'common' THEN 'common' WHEN 'team' THEN 'team' WHEN 'host' THEN 'moderator_dm' END
                    WHEN kind='player.private_message_sent' OR kind IN ('role.request_submitted','role.request_reviewed','role.request_withdrawn') THEN 'moderator_dm'
                    WHEN kind LIKE 'vote.%%' THEN 'vote'
                    WHEN kind IN ('content.notice_edited','content.notice_deleted','player.death_announced',
                        'player.revival_announced','announcement.published') THEN 'announcement' END AS channel_kind,
                CASE WHEN kind IN ('chat.sent','chat.revised') THEN CASE payload->>'channel'
                        WHEN 'common' THEN '' WHEN 'team' THEN payload->>'team_label' WHEN 'host' THEN payload->>'participant_user_id' END
                    WHEN kind='player.private_message_sent' THEN payload->>'recipient_user_id'
                    WHEN kind IN ('role.request_submitted','role.request_reviewed','role.request_withdrawn') THEN payload->'request'->>'user_id'
                    ELSE '' END AS channel_key
            FROM candidates c
        ) SELECT s.id AS source_event_id,s.room_id,s.user_id,s.position,s.scopes,
            CASE WHEN ch.id IS NULL THEN '[]'::jsonb ELSE jsonb_build_array(ch.id::text) END AS channel_ids
            FROM scoped s LEFT JOIN room_channels ch ON ch.room_id=s.room_id AND ch.kind=s.channel_kind AND ch.channel_key=s.channel_key
    """)
    op.execute("""
        INSERT INTO room_delivery_streams(room_id,user_id,position)
        SELECT room_id,user_id,max(position) FROM ux3_delivery_backfill GROUP BY room_id,user_id
    """)
    op.execute("""
        INSERT INTO room_deliveries(id,room_id,user_id,position,source_event_id,scopes,channel_ids)
        SELECT gen_random_uuid(),room_id,user_id,position,source_event_id,scopes,channel_ids FROM ux3_delivery_backfill
    """)


def downgrade() -> None:
    op.drop_table('room_deliveries')
    op.drop_table('room_delivery_streams')
