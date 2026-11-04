"""의견을 다섯 가지 유형으로 분류한다. 기존 의견은 OTHER로 보관한다."""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from alembic_git_revisions import get_down_revision

revision: str = "f54000000003"
down_revision: str | Sequence[str] | None = get_down_revision(revision)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("user_suggestions", sa.Column(
        "category", sa.String(16), nullable=False, server_default="OTHER"
    ))
    op.create_check_constraint(
        "ck_user_suggestions_category", "user_suggestions",
        "category IN ('POSITIVE', 'IMPROVEMENT', 'PROBLEM', 'QUESTION', 'OTHER')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_user_suggestions_category", "user_suggestions", type_="check")
    op.drop_column("user_suggestions", "category")
