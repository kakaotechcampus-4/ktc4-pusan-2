"""add closed to take end_reason

Revision ID: 5dffff19e4fc
Revises: 721ab9e9b803
Create Date: 2026-10-23 15:26:39.137240

"""
from collections.abc import Sequence

from alembic import op
from alembic_git_revisions import get_down_revision


# revision identifiers, used by Alembic.
revision: str = '5dffff19e4fc'
down_revision: str | Sequence[str] | None = get_down_revision(revision)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # 창을 닫아 끝난 Take 를 BE 가 끝낸 이유 (status 는 FAILED). ABANDONED 와 달리 되살리지 않는다
    op.drop_constraint('check_take_end_reason', 'takes', type_='check')
    op.create_check_constraint(
        'check_take_end_reason',
        'takes',
        "end_reason IN ('SILENCE', 'TIME_LIMIT', 'ABANDONED', 'CLOSED')",
    )


def downgrade() -> None:
    """Downgrade schema."""
    # 옛 CHECK 에 없는 값이다. 가장 가까운 ABANDONED 로 바꾼다 (상태 FAILED 는 그대로)
    op.execute("UPDATE takes SET end_reason = 'ABANDONED' WHERE end_reason = 'CLOSED'")
    op.drop_constraint('check_take_end_reason', 'takes', type_='check')
    op.create_check_constraint(
        'check_take_end_reason', 'takes', "end_reason IN ('SILENCE', 'TIME_LIMIT', 'ABANDONED')"
    )
