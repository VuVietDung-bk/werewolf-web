"""Moderator chat permission overrides, defaulting to the approved matrix.

Revision ID: 0019_chat_policy
Revises: 0018_chat_lookup_indexes
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0019_chat_policy"
down_revision = "0018_chat_lookup_indexes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("rooms", sa.Column("chat_policy", postgresql.JSONB(),
                                     server_default=sa.text("'{}'::jsonb"), nullable=False))


def downgrade() -> None:
    op.drop_column("rooms", "chat_policy")
