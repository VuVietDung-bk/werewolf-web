"""Link finished games to the next lobby while keeping each game's journal intact.

Revision ID: 0025_room_replay
Revises: 0024_chat_revisions
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0025_room_replay"
down_revision = "0024_chat_revisions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("rooms", sa.Column("next_room_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key("fk_rooms_next_room_id", "rooms", "rooms", ["next_room_id"], ["id"],
                          ondelete="SET NULL")


def downgrade() -> None:
    op.drop_constraint("fk_rooms_next_room_id", "rooms", type_="foreignkey")
    op.drop_column("rooms", "next_room_id")
