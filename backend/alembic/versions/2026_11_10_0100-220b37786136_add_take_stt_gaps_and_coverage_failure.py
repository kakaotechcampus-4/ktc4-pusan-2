"""Take 에 STT 끊김 구간과 대본 전달도가 끝내 실패한 이유를 남긴다.

둘 다 비어 있으면 모른다(이 칸을 만들기 전에 끝난 Take)거나 실패가 아니다.

Revision ID: 220b37786136
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from alembic_git_revisions import get_down_revision
from sqlalchemy.dialects import postgresql

revision: str = "220b37786136"
down_revision: str | Sequence[str] | None = get_down_revision(revision)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "takes",
        sa.Column("stt_gap_ranges", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column("takes", sa.Column("coverage_failure_code", sa.String(length=32), nullable=True))
    op.create_check_constraint(
        "check_take_coverage_failure_code",
        "takes",
        "coverage_failure_code IN "
        "('AI_TIMEOUT', 'AI_UNAVAILABLE', 'AI_ERROR', 'AI_REJECTED', 'AI_INVALID_RESPONSE')",
    )


def downgrade() -> None:
    op.drop_constraint("check_take_coverage_failure_code", "takes", type_="check")
    op.drop_column("takes", "coverage_failure_code")
    op.drop_column("takes", "stt_gap_ranges")
