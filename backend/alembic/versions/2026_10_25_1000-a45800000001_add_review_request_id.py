"""리뷰 실행 표식을 저장한다.

Revision ID: a45800000001
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from alembic_git_revisions import get_down_revision

revision: str = "a45800000001"
down_revision: str | Sequence[str] | None = get_down_revision(revision)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("takes", sa.Column("review_request_id", sa.Uuid(), nullable=True))


def downgrade() -> None:
    op.drop_column("takes", "review_request_id")
