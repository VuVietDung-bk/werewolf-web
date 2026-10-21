"""Editable presentation of moderator messages and public notices.

Revision ID: 0012_content_revisions
Revises: 0011_role_private
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0012_content_revisions"
down_revision = "0011_role_private"
branch_labels = None
depends_on = None


def upgrade() -> None:
    uuid = postgresql.UUID(as_uuid=True)
    op.create_table(
        "content_revisions",
        sa.Column("source_event_id", uuid, sa.ForeignKey("game_events.id", ondelete="CASCADE"),
                  primary_key=True),
        sa.Column("room_id", uuid, sa.ForeignKey("rooms.id", ondelete="CASCADE"), nullable=False),
        sa.Column("current_text", sa.String(2000)),
        sa.Column("edited_at", sa.DateTime(timezone=True)),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_content_revisions_room", "content_revisions", ["room_id"])


def downgrade() -> None:
    op.drop_index("ix_content_revisions_room", table_name="content_revisions")
    op.drop_table("content_revisions")
