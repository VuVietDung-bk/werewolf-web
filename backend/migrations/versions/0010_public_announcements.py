"""Separate public player status and public change counter from private events.

Revision ID: 0010_public_announcements
Revises: 0009_player_state
"""

from alembic import op
import sqlalchemy as sa

revision = "0010_public_announcements"
down_revision = "0009_player_state"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("rooms", sa.Column("public_version", sa.Integer(), nullable=False, server_default="0"))
    op.execute(sa.text("""
        UPDATE rooms AS r SET public_version = (
            SELECT COUNT(*) FROM game_events AS e
            WHERE e.room_id = r.id AND e.visibility = 'room'
        )
    """))
    op.add_column("player_states", sa.Column("public_is_alive", sa.Boolean(),
                                              nullable=False, server_default=sa.true()))
    op.add_column("player_states", sa.Column("public_role_name", sa.String(80)))


def downgrade() -> None:
    op.drop_column("player_states", "public_role_name")
    op.drop_column("player_states", "public_is_alive")
    op.drop_column("rooms", "public_version")
