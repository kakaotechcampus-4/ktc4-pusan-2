"""대화 행동 args 칸을 json 으로 바꿔 받은 숫자 표기를 그대로 보관한다(args_hash 재계산과 같게).

Revision ID: c81300000008
"""

from collections.abc import Sequence

from alembic import op
from alembic_git_revisions import get_down_revision
from sqlalchemy.dialects import postgresql

revision: str = "c81300000008"
down_revision: str | Sequence[str] | None = get_down_revision(revision)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "chat_actions",
        "args",
        type_=postgresql.JSON(),
        existing_type=postgresql.JSONB(),
        existing_nullable=False,
        postgresql_using="args::json",
    )


def downgrade() -> None:
    op.alter_column(
        "chat_actions",
        "args",
        type_=postgresql.JSONB(),
        existing_type=postgresql.JSON(),
        existing_nullable=False,
        postgresql_using="args::jsonb",
    )
