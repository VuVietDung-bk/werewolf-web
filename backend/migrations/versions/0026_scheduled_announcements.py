"""Queue private moderator notices until the next phase transition.

Revision ID: 0026_scheduled_announcements
Revises: 0025_room_replay
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0026_scheduled_announcements"
down_revision = "0025_room_replay"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("ck_player_states_publication_disposition", "player_states", type_="check")
    op.create_check_constraint("ck_player_states_publication_disposition", "player_states",
                               "publication_disposition IN ('published', 'pending', 'held', 'skipped', 'scheduled')")
    op.create_table(
        "scheduled_announcements",
        sa.Column("id", postgresql.UUID(as_uuid=True), sa.ForeignKey("game_events.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("room_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("rooms.id", ondelete="CASCADE"), nullable=False),
        sa.Column("player_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("kind IN ('status', 'custom')", name="ck_scheduled_announcements_kind"),
    )
    op.create_index("ix_scheduled_announcements_room", "scheduled_announcements", ["room_id", "created_at"])
    op.create_index("uq_scheduled_announcements_player", "scheduled_announcements", ["room_id", "player_id"],
                    unique=True, postgresql_where=sa.text("player_id IS NOT NULL"))


def downgrade() -> None:
    op.drop_index("uq_scheduled_announcements_player", table_name="scheduled_announcements")
    op.drop_index("ix_scheduled_announcements_room", table_name="scheduled_announcements")
    op.drop_table("scheduled_announcements")
    op.drop_constraint("ck_player_states_publication_disposition", "player_states", type_="check")
    op.execute("UPDATE player_states SET publication_disposition = 'pending' "
               "WHERE publication_disposition = 'scheduled'")
    op.create_check_constraint("ck_player_states_publication_disposition", "player_states",
                               "publication_disposition IN ('published', 'pending', 'held', 'skipped')")
