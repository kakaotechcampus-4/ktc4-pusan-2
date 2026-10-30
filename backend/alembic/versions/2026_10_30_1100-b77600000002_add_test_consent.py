"""사용자 테스트 참여 동의 시각과 안내문 버전을 저장한다.

Revision ID: b77600000002
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from alembic_git_revisions import get_down_revision

revision: str = "b77600000002"
down_revision: str | Sequence[str] | None = get_down_revision(revision)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("test_consent_at", sa.DateTime(timezone=True)))
    op.add_column("users", sa.Column("test_consent_version", sa.Integer()))
    op.create_check_constraint(
        "ck_users_test_consent",
        "users",
        "(test_consent_at IS NULL) = (test_consent_version IS NULL)",
    )


def downgrade() -> None:
    op.drop_constraint("ck_users_test_consent", "users", type_="check")
    op.drop_column("users", "test_consent_version")
    op.drop_column("users", "test_consent_at")
