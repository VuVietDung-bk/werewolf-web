"""Store the moderator's Seer result for each role.

Revision ID: 0005_role_seer_result
Revises: 0004_roles
"""

from alembic import op
import sqlalchemy as sa

revision = "0005_role_seer_result"
down_revision = "0004_roles"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("role_definitions", sa.Column(
        "seer_result", sa.String(80), nullable=False, server_default="Chưa thiết lập"
    ))


def downgrade() -> None:
    op.drop_column("role_definitions", "seer_result")
