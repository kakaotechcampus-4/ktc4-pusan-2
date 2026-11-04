"""회원 탈퇴 뒤에도 서비스 평점·의견을 보관한다."""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from alembic_git_revisions import get_down_revision

revision: str = "f54000000002"
down_revision: str | Sequence[str] | None = get_down_revision(revision)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

FOREIGN_KEYS = (
    ("service_feedbacks", "service_feedbacks_user_id_fkey"),
    ("user_suggestions", "fk_user_suggestions_user"),
)


def upgrade() -> None:
    for table, constraint in FOREIGN_KEYS:
        op.drop_constraint(constraint, table, type_="foreignkey")
        op.alter_column(table, "user_id", existing_type=sa.Uuid(), nullable=True)
        op.create_foreign_key(constraint, table, "users", ["user_id"], ["id"], ondelete="SET NULL")


def downgrade() -> None:
    has_retained = op.get_bind().scalar(sa.text(
        "SELECT EXISTS (SELECT 1 FROM service_feedbacks WHERE user_id IS NULL) "
        "OR EXISTS (SELECT 1 FROM user_suggestions WHERE user_id IS NULL)"
    ))
    if has_retained:
        raise RuntimeError("작성자 연결이 해제된 평점·의견이 있어 이전 스키마로 되돌릴 수 없습니다.")
    for table, constraint in FOREIGN_KEYS:
        op.drop_constraint(constraint, table, type_="foreignkey")
        op.alter_column(table, "user_id", existing_type=sa.Uuid(), nullable=False)
        op.create_foreign_key(constraint, table, "users", ["user_id"], ["id"], ondelete="CASCADE")
