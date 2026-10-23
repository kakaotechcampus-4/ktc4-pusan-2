"""add check constraints to takes

Revision ID: b669397959e1
Revises: f1fe021ddc00
Create Date: 2026-10-22 21:13:26.805192

"""
from collections.abc import Sequence

from alembic import op
from alembic_git_revisions import get_down_revision

# revision identifiers, used by Alembic.
revision: str = 'b669397959e1'
down_revision: str | Sequence[str] | None = get_down_revision(revision)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # 엔티티에 CHECK 가 있었지만 __table_args__ 밖이라 테이블에 걸리지 않았다.
    # 그동안 테스트 · 목에서 쓰던 옛 값은 같은 뜻의 값으로 바꾼다. 다른 값이 남아 있으면
    # 아래에서 실패한다 — 어떤 값인지 보고 정한다
    op.execute("UPDATE takes SET mode = 'COACHING' WHERE mode = 'PRACTICE'")
    op.execute("UPDATE takes SET script_mode = 'HIGHLIGHT' WHERE script_mode = 'FULL'")
    op.create_check_constraint(
        'check_take_status',
        'takes',
        "status IN ('READY', 'RUNNING', 'ANALYZING', 'COMPLETED', 'FAILED')",
    )
    op.create_check_constraint('check_take_mode', 'takes', "mode IN ('COACHING', 'EXAM')")
    op.create_check_constraint(
        'check_take_script_mode', 'takes', "script_mode IN ('HIGHLIGHT', 'KEYWORD', 'OFF')"
    )
    op.create_check_constraint(
        'check_take_end_reason', 'takes', "end_reason IN ('SILENCE', 'TIME_LIMIT')"
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint('check_take_end_reason', 'takes', type_='check')
    op.drop_constraint('check_take_script_mode', 'takes', type_='check')
    op.drop_constraint('check_take_mode', 'takes', type_='check')
    op.drop_constraint('check_take_status', 'takes', type_='check')
