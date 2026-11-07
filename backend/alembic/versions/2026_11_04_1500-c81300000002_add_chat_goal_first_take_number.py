"""대화 목표에 확인 대상 Take 번호 경계를 더한다.

Revision ID: c81300000002
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from alembic_git_revisions import get_down_revision

revision: str = "c81300000002"
down_revision: str | Sequence[str] | None = get_down_revision(revision)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("chat_goals", sa.Column("first_take_number", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("chat_goals", "first_take_number")
