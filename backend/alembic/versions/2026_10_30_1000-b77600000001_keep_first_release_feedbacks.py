"""1차 배포 서비스 별점을 이름을 바꿔 보관하고 홈 팝업 표시 기록을 지운다.

Revision ID: b77600000001
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from alembic_git_revisions import get_down_revision

revision: str = "b77600000001"
down_revision: str | Sequence[str] | None = get_down_revision(revision)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD, NEW = "service_feedbacks", "first_release_feedbacks"
# (옛 이름, 새 이름). UNIQUE 는 이름을 바꾸면 같은 이름의 인덱스도 따라 바뀐다
CONSTRAINTS = (
    ("service_feedbacks_pkey", "first_release_feedbacks_pkey"),
    ("uq_service_feedbacks_user", "uq_first_release_feedbacks_user"),
    ("ck_service_feedbacks_rating", "ck_first_release_feedbacks_rating"),
    ("service_feedbacks_user_id_fkey", "fk_first_release_feedbacks_user"),
)
INDEXES = (("ix_service_feedbacks_created", "ix_first_release_feedbacks_created"),)


def upgrade() -> None:
    # 홈 팝업을 몇 번 띄웠는지만 담은 기록이다. 설문을 자동으로 띄우지 않게 되어 쓸 곳이 없다
    op.drop_table("service_feedback_impressions")
    # user_suggestions 의 FK 는 테이블을 따라가므로 다시 걸지 않는다
    op.rename_table(OLD, NEW)
    for old, new in CONSTRAINTS:
        op.execute(f"ALTER TABLE {NEW} RENAME CONSTRAINT {old} TO {new}")
    for old, new in INDEXES:
        op.execute(f"ALTER INDEX {old} RENAME TO {new}")


def downgrade() -> None:
    for old, new in INDEXES:
        op.execute(f"ALTER INDEX {new} RENAME TO {old}")
    for old, new in CONSTRAINTS:
        op.execute(f"ALTER TABLE {NEW} RENAME CONSTRAINT {new} TO {old}")
    op.rename_table(NEW, OLD)
    # 지운 표시 기록은 되살리지 못한다. 빈 테이블만 만든다
    op.create_table(
        "service_feedback_impressions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("display_request_id", sa.Uuid(), nullable=False),
        sa.Column("display_number", sa.Integer(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint(
            "user_id", "display_request_id", name="uq_service_feedback_impressions_request"
        ),
        sa.UniqueConstraint(
            "user_id", "display_number", name="uq_service_feedback_impressions_number"
        ),
        sa.CheckConstraint(
            "display_number BETWEEN 1 AND 3", name="ck_service_feedback_impressions_number"
        ),
    )
