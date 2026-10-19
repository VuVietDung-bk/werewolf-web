"""Room role templates, immutable distribution snapshot, and private assignments.

Revision ID: 0004_roles
Revises: 0003_room_seats
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0004_roles"
down_revision = "0003_room_seats"
branch_labels = None
depends_on = None


def upgrade() -> None:
    uuid = postgresql.UUID(as_uuid=True)
    op.create_table(
        "role_sets",
        sa.Column("room_id", uuid, sa.ForeignKey("rooms.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("name", sa.String(120), nullable=False, server_default="Bộ role của phòng"),
    )
    op.execute("INSERT INTO role_sets (room_id) SELECT id FROM rooms")
    op.create_table(
        "role_definitions",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("room_id", uuid, sa.ForeignKey("role_sets.room_id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("description", sa.String(4000), nullable=False),
        sa.Column("faction_label", sa.String(80), nullable=False),
        sa.Column("team_chat_label", sa.String(80)),
        sa.Column("quantity", sa.SmallInteger(), nullable=False, server_default="0"),
        sa.Column("role_kind", sa.String(16), nullable=False),
        sa.Column("request_phases", postgresql.JSONB(), nullable=False),
        sa.Column("request_when_dead", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("host_contact", postgresql.JSONB(), nullable=False),
        sa.UniqueConstraint("room_id", "name", name="uq_role_definitions_room_name"),
        sa.CheckConstraint("quantity BETWEEN 0 AND 30", name="ck_role_definitions_quantity"),
    )
    op.create_table(
        "role_snapshots",
        sa.Column("room_id", uuid, sa.ForeignKey("rooms.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("set_name", sa.String(120), nullable=False),
        sa.Column("definitions", postgresql.JSONB(), nullable=False),
        sa.Column("distribution_mode", sa.String(24), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_table(
        "role_assignments",
        sa.Column("room_id", uuid, sa.ForeignKey("role_snapshots.room_id", ondelete="CASCADE"), primary_key=True),
        sa.Column("user_id", uuid, primary_key=True),
        sa.Column("role_id", uuid, nullable=False),
        sa.ForeignKeyConstraint(["room_id", "user_id"], ["room_members.room_id", "room_members.user_id"], ondelete="CASCADE"),
    )
    op.create_index("ix_role_assignments_room_role", "role_assignments", ["room_id", "role_id"])


def downgrade() -> None:
    op.drop_index("ix_role_assignments_room_role", table_name="role_assignments")
    op.drop_table("role_assignments")
    op.drop_table("role_snapshots")
    op.drop_table("role_definitions")
    op.drop_table("role_sets")
