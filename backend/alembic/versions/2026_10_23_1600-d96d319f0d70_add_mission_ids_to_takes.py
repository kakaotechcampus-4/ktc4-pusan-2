"""add mission_ids to takes

Revision ID: d96d319f0d70
Revises: 2166f2e740d7
Create Date: 2026-10-23 16:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from alembic_git_revisions import get_down_revision
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'd96d319f0d70'
down_revision: str | Sequence[str] | None = get_down_revision(revision)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('takes', sa.Column('mission_ids', postgresql.JSONB(), nullable=True))
    op.execute(
        "UPDATE takes SET mission_ids = m.ids "
        "FROM (SELECT take_id, jsonb_agg(id::text) AS ids FROM missions "
        "WHERE take_id IS NOT NULL GROUP BY take_id) AS m "
        "WHERE takes.id = m.take_id"
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('takes', 'mission_ids')
