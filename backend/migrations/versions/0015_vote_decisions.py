"""Keep moderator vote decisions private until explicit publication.

Revision ID: 0015_vote_decisions
Revises: 0014_vote_rounds
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0015_vote_decisions"
down_revision = "0014_vote_rounds"
branch_labels = None
depends_on = None


def upgrade() -> None:
    uuid = postgresql.UUID(as_uuid=True)
    op.add_column("vote_rounds", sa.Column("decision_text", sa.String(2000)))
    op.add_column("vote_rounds", sa.Column("decision_reason", sa.String(1000)))
    op.add_column("vote_rounds", sa.Column("decision_target_user_id", uuid))
    op.add_column("vote_rounds", sa.Column("decision_event_id", uuid))
    op.add_column("vote_rounds", sa.Column("decided_at", sa.DateTime(timezone=True)))
    op.create_foreign_key("fk_vote_rounds_decision_target", "vote_rounds", "users",
                          ["decision_target_user_id"], ["id"], ondelete="RESTRICT")
    op.create_foreign_key("fk_vote_rounds_decision_event", "vote_rounds", "game_events",
                          ["decision_event_id"], ["id"], ondelete="SET NULL")
    op.create_unique_constraint("uq_vote_rounds_decision_event", "vote_rounds",
                                ["decision_event_id"])


def downgrade() -> None:
    op.drop_constraint("uq_vote_rounds_decision_event", "vote_rounds", type_="unique")
    op.drop_constraint("fk_vote_rounds_decision_event", "vote_rounds", type_="foreignkey")
    op.drop_constraint("fk_vote_rounds_decision_target", "vote_rounds", type_="foreignkey")
    op.drop_column("vote_rounds", "decided_at")
    op.drop_column("vote_rounds", "decision_event_id")
    op.drop_column("vote_rounds", "decision_target_user_id")
    op.drop_column("vote_rounds", "decision_reason")
    op.drop_column("vote_rounds", "decision_text")
