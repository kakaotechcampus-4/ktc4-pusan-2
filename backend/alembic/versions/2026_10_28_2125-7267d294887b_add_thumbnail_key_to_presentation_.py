"""발표자료 첫 장 썸네일의 S3 키를 저장한다.

Revision ID: 7267d294887b
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from alembic_git_revisions import get_down_revision

revision: str = "7267d294887b"
down_revision: str | Sequence[str] | None = get_down_revision(revision)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "presentation_versions", sa.Column("thumbnail_key", sa.String(255), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("presentation_versions", "thumbnail_key")
