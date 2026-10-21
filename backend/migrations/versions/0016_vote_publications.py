"""Publish only moderator-selected vote details.

Revision ID: 0016_vote_publications
Revises: 0015_vote_decisions
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0016_vote_publications"
down_revision = "0015_vote_decisions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("vote_rounds", sa.Column("publication_event_id", postgresql.UUID(as_uuid=True)))
    op.add_column("vote_rounds", sa.Column("published_at", sa.DateTime(timezone=True)))
    op.create_foreign_key("fk_vote_rounds_publication_event", "vote_rounds", "game_events",
                          ["publication_event_id"], ["id"], ondelete="SET NULL")
    op.create_unique_constraint("uq_vote_rounds_publication_event", "vote_rounds",
                                ["publication_event_id"])


def downgrade() -> None:
    op.drop_constraint("uq_vote_rounds_publication_event", "vote_rounds", type_="unique")
    op.drop_constraint("fk_vote_rounds_publication_event", "vote_rounds", type_="foreignkey")
    op.drop_column("vote_rounds", "published_at")
    op.drop_column("vote_rounds", "publication_event_id")
