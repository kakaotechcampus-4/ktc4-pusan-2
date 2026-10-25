"""add abandoned to take end_reason

Revision ID: 8437662db331
Revises: b669397959e1
Create Date: 2026-10-22 21:30:17.975979

"""
from collections.abc import Sequence

from alembic import op
from alembic_git_revisions import get_down_revision


# revision identifiers, used by Alembic.
revision: str = '8437662db331'
down_revision: str | Sequence[str] | None = get_down_revision(revision)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # 탭을 닫고 떠난 Take 를 BE 가 끝낸 이유 (status 는 FAILED)
    op.drop_constraint('check_take_end_reason', 'takes', type_='check')
    op.create_check_constraint(
        'check_take_end_reason', 'takes', "end_reason IN ('SILENCE', 'TIME_LIMIT', 'ABANDONED')"
    )


def downgrade() -> None:
    """Downgrade schema."""
    # 옛 CHECK 에 없는 값이다. 상태(FAILED)는 남기고 이유만 지운다
    op.execute("UPDATE takes SET end_reason = NULL WHERE end_reason = 'ABANDONED'")
    op.drop_constraint('check_take_end_reason', 'takes', type_='check')
    op.create_check_constraint(
        'check_take_end_reason', 'takes', "end_reason IN ('SILENCE', 'TIME_LIMIT')"
    )
