"""Allow an empty seat to be reused while preserving departed members.

Revision ID: 0003_room_seats
Revises: 0002_event_journal
"""

from alembic import op
import sqlalchemy as sa

revision = "0003_room_seats"
down_revision = "0002_event_journal"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("uq_room_members_seat", "room_members", type_="unique")
    op.create_check_constraint(
        "ck_room_members_active_has_seat", "room_members",
        "left_at IS NOT NULL OR seat_number IS NOT NULL",
    )
    op.create_index(
        "uq_room_members_active_seat", "room_members", ["room_id", "seat_number"],
        unique=True, postgresql_where=sa.text("left_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_room_members_active_seat", table_name="room_members")
    op.drop_constraint("ck_room_members_active_has_seat", "room_members", type_="check")
    # A downgraded schema cannot represent repeated historical seat numbers.
    op.create_unique_constraint("uq_room_members_seat", "room_members", ["room_id", "seat_number"])
