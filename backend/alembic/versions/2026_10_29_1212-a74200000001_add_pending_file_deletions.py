"""발표를 지운 뒤 S3 에서 아직 지우지 못한 경로를 남겨 성공할 때까지 다시 지운다.

Revision ID: a74200000001
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a74200000001"
# 썸네일이 이미 적용된 DB 에서도 이 테이블을 만들어야 한다. git 순서에 따라 기존 head 앞에
# 끼어들면 upgrade head 가 건너뛰므로, 먼저 머지된 썸네일 마이그레이션 뒤로 고정한다
down_revision: str = "7267d294887b"
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
