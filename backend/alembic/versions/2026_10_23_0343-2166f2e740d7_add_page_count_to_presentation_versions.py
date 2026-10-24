"""add page_count to presentation_versions

Revision ID: 2166f2e740d7
Revises: 4fd2f8cf7ffa
Create Date: 2026-10-23 03:43:39.051455

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from alembic_git_revisions import get_down_revision


# revision identifiers, used by Alembic.
revision: str = '2166f2e740d7'
down_revision: str | Sequence[str] | None = get_down_revision(revision)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # 이미 올린 발표자료는 비어 있다. S3 에서 PDF 를 받아야 해서 마이그레이션이 아니라
    # dev/backfill-presentation-page-count.py 로 채운다
    op.add_column('presentation_versions', sa.Column('page_count', sa.Integer(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('presentation_versions', 'page_count')
