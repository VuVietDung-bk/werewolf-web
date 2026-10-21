"""Keep moderator note presentation separate from its original text.

Revision ID: 0013_note_revisions
Revises: 0012_content_revisions
"""

from alembic import op
import sqlalchemy as sa

revision = "0013_note_revisions"
down_revision = "0012_content_revisions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("player_notes", sa.Column("current_text", sa.String(2000)))
    op.add_column("player_notes", sa.Column("edited_at", sa.DateTime(timezone=True)))
    op.add_column("player_notes", sa.Column("deleted_at", sa.DateTime(timezone=True)))


def downgrade() -> None:
    op.drop_column("player_notes", "deleted_at")
    op.drop_column("player_notes", "edited_at")
    op.drop_column("player_notes", "current_text")
