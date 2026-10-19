"""Private per-player state and moderator notes.

Revision ID: 0009_player_state
Revises: 0008_game_flow
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0009_player_state"
down_revision = "0008_game_flow"
branch_labels = None
depends_on = None


def upgrade() -> None:
    uuid = postgresql.UUID(as_uuid=True)
    op.create_table(
        "player_states",
        sa.Column("room_id", uuid, primary_key=True),
        sa.Column("user_id", uuid, primary_key=True),
        sa.Column("is_alive", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("effects", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.ForeignKeyConstraint(["room_id", "user_id"],
                                ["role_assignments.room_id", "role_assignments.user_id"],
                                ondelete="CASCADE"),
    )
    op.create_table(
        "player_notes",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("room_id", uuid, nullable=False),
        sa.Column("user_id", uuid, nullable=False),
        sa.Column("text", sa.String(2000), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["room_id", "user_id"],
                                ["player_states.room_id", "player_states.user_id"],
                                ondelete="CASCADE"),
    )
    op.create_index("ix_player_notes_room_user_created", "player_notes",
                    ["room_id", "user_id", "created_at"])
    # Existing active and finished matches need their original players on the board.
    op.execute(sa.text("""
        INSERT INTO player_states (room_id, user_id)
        SELECT a.room_id, a.user_id FROM role_assignments AS a
        JOIN rooms AS r ON r.id = a.room_id
        WHERE r.status IN ('playing', 'finished')
    """))


def downgrade() -> None:
    op.drop_index("ix_player_notes_room_user_created", table_name="player_notes")
    op.drop_table("player_notes")
    op.drop_table("player_states")
