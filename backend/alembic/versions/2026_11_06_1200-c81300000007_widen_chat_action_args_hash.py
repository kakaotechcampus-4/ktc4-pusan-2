"""대화 행동 args_hash 칸을 'sha256:' + 16진수 64자리 해시가 넉넉히 들어가게 늘린다.

Revision ID: c81300000007
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from alembic_git_revisions import get_down_revision

revision: str = "c81300000007"
down_revision: str | Sequence[str] | None = get_down_revision(revision)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "chat_actions",
        "args_hash",
        type_=sa.String(length=100),
        existing_type=sa.String(length=64),
        existing_nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "chat_actions",
        "args_hash",
        type_=sa.String(length=64),
        existing_type=sa.String(length=100),
        existing_nullable=True,
    )
