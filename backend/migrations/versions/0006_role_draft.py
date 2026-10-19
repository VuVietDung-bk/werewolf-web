"""Persist 1-of-3 draft configuration, turn order, offers, and choices.

Revision ID: 0006_role_draft
Revises: 0005_role_seer_result
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0006_role_draft"
down_revision = "0005_role_seer_result"
branch_labels = None
depends_on = None


def upgrade() -> None:
    uuid = postgresql.UUID(as_uuid=True)
    op.add_column("role_snapshots", sa.Column(
        "settings", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
    ))
    op.create_table(
        "draft_turns",
        sa.Column("room_id", uuid, sa.ForeignKey("role_snapshots.room_id", ondelete="CASCADE"), primary_key=True),
        sa.Column("turn_index", sa.SmallInteger(), primary_key=True),
        sa.Column("user_id", uuid, nullable=False),
        sa.Column("offers", postgresql.JSONB()),
        sa.Column("chosen_role_id", uuid),
        sa.Column("chosen_by_user_id", uuid, sa.ForeignKey("users.id", ondelete="RESTRICT")),
        sa.Column("choice_mode", sa.String(24)),
        sa.Column("choice_reason", sa.String(1000)),
        sa.Column("offered_at", sa.DateTime(timezone=True)),
        sa.Column("chosen_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(["room_id", "user_id"], ["room_members.room_id", "room_members.user_id"], ondelete="CASCADE"),
        sa.UniqueConstraint("room_id", "user_id", name="uq_draft_turns_room_user"),
        sa.CheckConstraint("turn_index >= 1", name="ck_draft_turns_index"),
    )


def downgrade() -> None:
    op.drop_table("draft_turns")
    op.drop_column("role_snapshots", "settings")
