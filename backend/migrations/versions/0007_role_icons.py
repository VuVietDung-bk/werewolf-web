"""Add built-in icon choices and persistent room-owned uploaded images.

Revision ID: 0007_role_icons
Revises: 0006_role_draft
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0007_role_icons"
down_revision = "0006_role_draft"
branch_labels = None
depends_on = None


def upgrade() -> None:
    uuid = postgresql.UUID(as_uuid=True)
    op.create_table(
        "role_icons",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("room_id", uuid, sa.ForeignKey("rooms.id", ondelete="CASCADE"), nullable=False),
        sa.Column("image_bytes", sa.LargeBinary(), nullable=False),
        sa.Column("width", sa.SmallInteger(), nullable=False),
        sa.Column("height", sa.SmallInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.add_column("role_definitions", sa.Column(
        "icon_key", sa.String(24), nullable=False, server_default="auto"
    ))
    op.add_column("role_definitions", sa.Column("icon_id", uuid))
    op.create_foreign_key("fk_role_definitions_icon_id", "role_definitions", "role_icons",
                          ["icon_id"], ["id"], ondelete="SET NULL")


def downgrade() -> None:
    op.drop_constraint("fk_role_definitions_icon_id", "role_definitions", type_="foreignkey")
    op.drop_column("role_definitions", "icon_id")
    op.drop_column("role_definitions", "icon_key")
    op.drop_table("role_icons")
