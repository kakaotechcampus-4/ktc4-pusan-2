"""발표를 지운 뒤 S3 에서 아직 지우지 못한 경로를 남겨 성공할 때까지 다시 지운다.

Revision ID: a74200000001
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from alembic_git_revisions import get_down_revision

revision: str = "a74200000001"
down_revision: str | Sequence[str] | None = get_down_revision(revision)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "pending_file_deletions",
        sa.Column("prefix", sa.String(length=255), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.PrimaryKeyConstraint("prefix"),
    )


def downgrade() -> None:
    op.drop_table("pending_file_deletions")
