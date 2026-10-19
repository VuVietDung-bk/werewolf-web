"""Store paused state and the moderator's final result.

Revision ID: 0008_game_flow
Revises: 0007_role_icons
"""

from alembic import op
import sqlalchemy as sa

revision = "0008_game_flow"
down_revision = "0007_role_icons"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("rooms", sa.Column("is_paused", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("rooms", sa.Column("result_label", sa.String(120)))
    op.add_column("rooms", sa.Column("result_reason", sa.String(2000)))
    op.add_column("rooms", sa.Column("ended_at", sa.DateTime(timezone=True)))


def downgrade() -> None:
    op.drop_column("rooms", "ended_at")
    op.drop_column("rooms", "result_reason")
    op.drop_column("rooms", "result_label")
    op.drop_column("rooms", "is_paused")
