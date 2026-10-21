"""Manual daytime vote rounds and current ballots.

Revision ID: 0014_vote_rounds
Revises: 0013_note_revisions
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0014_vote_rounds"
down_revision = "0013_note_revisions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    uuid = postgresql.UUID(as_uuid=True)
    op.create_table(
        "vote_rounds",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("room_id", uuid, sa.ForeignKey("rooms.id", ondelete="CASCADE"), nullable=False),
        sa.Column("day_number", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="open"),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("closed_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("day_number >= 0", name="ck_vote_rounds_day_number"),
    )
    op.create_index("uq_vote_rounds_one_open", "vote_rounds", ["room_id"], unique=True,
                    postgresql_where=sa.text("status = 'open'"))
    op.create_table(
        "vote_ballots",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("vote_round_id", uuid, sa.ForeignKey("vote_rounds.id", ondelete="CASCADE"), nullable=False),
        sa.Column("voter_user_id", uuid, sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("choice", sa.String(16), nullable=False),
        sa.Column("target_user_id", uuid, sa.ForeignKey("users.id", ondelete="RESTRICT")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("vote_round_id", "voter_user_id", name="uq_vote_ballots_voter"),
        sa.CheckConstraint("choice IN ('target', 'skip', 'withdrawn')", name="ck_vote_ballots_choice"),
        sa.CheckConstraint("(choice = 'target') = (target_user_id IS NOT NULL)",
                           name="ck_vote_ballots_target"),
    )


def downgrade() -> None:
    op.drop_table("vote_ballots")
    op.drop_index("uq_vote_rounds_one_open", table_name="vote_rounds")
    op.drop_table("vote_rounds")
