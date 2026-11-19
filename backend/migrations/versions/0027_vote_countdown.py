"""Optional vote deadline.

Revision ID: 0027_vote_countdown
Revises: 0026_scheduled_announcements
"""

from alembic import op
import sqlalchemy as sa

revision = "0027_vote_countdown"
down_revision = "0026_scheduled_announcements"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("vote_rounds", sa.Column("closes_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("vote_rounds", "closes_at")
