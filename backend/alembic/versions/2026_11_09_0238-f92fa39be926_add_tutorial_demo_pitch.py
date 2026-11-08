"""튜토리얼 샘플 발표 표시와 튜토리얼 리포트를 받은 시각을 남긴다.

샘플 발표는 사용자마다 하나라서 is_demo 인 행에만 사용자 유일 색인을 건다.

Revision ID: f92fa39be926
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from alembic_git_revisions import get_down_revision

revision: str = "f92fa39be926"
down_revision: str | Sequence[str] | None = get_down_revision(revision)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "pitches",
        sa.Column("is_demo", sa.Boolean(), server_default="false", nullable=False),
    )
    op.create_index(
        "uq_pitches_user_demo",
        "pitches",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("is_demo"),
    )
    op.add_column(
        "users",
        sa.Column("tutorial_report_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("users", "tutorial_report_at")
    op.drop_index("uq_pitches_user_demo", table_name="pitches")
    op.drop_column("pitches", "is_demo")
