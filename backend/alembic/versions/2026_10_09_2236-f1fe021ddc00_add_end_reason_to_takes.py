"""add end_reason to takes

Revision ID: f1fe021ddc00
Revises: d82be488a00c
Create Date: 2026-10-09 22:36:11.884012

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from alembic_git_revisions import get_down_revision


# revision identifiers, used by Alembic.
revision: str = 'f1fe021ddc00'
down_revision: str | Sequence[str] | None = get_down_revision(revision)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('takes', sa.Column('end_reason', sa.String(length=20), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('takes', 'end_reason')
