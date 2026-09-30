"""make take goal_time_sec not null

Revision ID: 17e06a675e53
Revises: 11215228fecd
Create Date: 2026-09-30 16:23:35.644598

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from alembic_git_revisions import get_down_revision


# revision identifiers, used by Alembic.
revision: str = '17e06a675e53'
down_revision: str | Sequence[str] | None = get_down_revision(revision)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("""
        UPDATE takes t
        SET goal_time_sec = p.time_limit_sec
        FROM pitches p
        WHERE t.pitch_id = p.id AND t.goal_time_sec IS NULL
    """)
    op.alter_column('takes', 'goal_time_sec',
                    existing_type=sa.Integer(), nullable=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.alter_column('takes', 'goal_time_sec',
                    existing_type=sa.Integer(), nullable=True)
