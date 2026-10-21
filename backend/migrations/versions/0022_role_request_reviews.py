"""Moderator replies and review history for role requests.

Revision ID: 0022_role_request_reviews
Revises: 0021_role_requests
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0022_role_request_reviews"
down_revision = "0021_role_requests"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("role_requests", sa.Column("updated_sequence", sa.BigInteger(), nullable=True))
    op.execute("UPDATE role_requests SET updated_sequence = sequence")
    op.alter_column("role_requests", "updated_sequence", nullable=False)
    op.add_column("role_requests", sa.Column("reply_text", sa.String(2000), nullable=True))
    op.add_column("role_requests", sa.Column("linked_event_ids", postgresql.JSONB(),
                                            server_default=sa.text("'[]'::jsonb"), nullable=False))
    op.add_column("role_requests", sa.Column("updated_at", sa.DateTime(timezone=True),
                                            server_default=sa.func.now(), nullable=False))
    op.create_index("ix_role_requests_room_updated_sequence", "role_requests",
                    ["room_id", "updated_sequence"])


def downgrade() -> None:
    op.drop_index("ix_role_requests_room_updated_sequence", table_name="role_requests")
    op.drop_column("role_requests", "updated_at")
    op.drop_column("role_requests", "linked_event_ids")
    op.drop_column("role_requests", "reply_text")
    op.drop_column("role_requests", "updated_sequence")
