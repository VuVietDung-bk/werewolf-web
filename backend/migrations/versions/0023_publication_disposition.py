"""Track moderator choice to hold or skip a status announcement.

Revision ID: 0023_publication_disposition
Revises: 0022_role_request_reviews
"""

from alembic import op
import sqlalchemy as sa

revision = "0023_publication_disposition"
down_revision = "0022_role_request_reviews"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("player_states", sa.Column("publication_disposition", sa.String(16),
                                            nullable=False, server_default="published"))
    op.execute("UPDATE player_states SET publication_disposition = 'pending' "
               "WHERE is_alive <> public_is_alive")
    op.create_check_constraint("ck_player_states_publication_disposition", "player_states",
                               "publication_disposition IN ('published', 'pending', 'held', 'skipped')")


def downgrade() -> None:
    op.drop_constraint("ck_player_states_publication_disposition", "player_states", type_="check")
    op.drop_column("player_states", "publication_disposition")
