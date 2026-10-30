"""1차 배포 홈 팝업의 서비스 별점과 표시 기록을 지운다. 마이페이지 의견은 남긴다.

Revision ID: b77600000004
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from alembic_git_revisions import get_down_revision

revision: str = "b77600000004"
down_revision: str | Sequence[str] | None = get_down_revision(revision)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

MY_PAGE_ONLY = "source = 'MY_PAGE' AND request_id IS NOT NULL"
WITH_POPUP = (
    "(source = 'RATING_POPUP' AND service_feedback_id IS NOT NULL AND request_id IS NULL) "
    "OR (source = 'MY_PAGE' AND service_feedback_id IS NULL AND request_id IS NOT NULL)"
)


def upgrade() -> None:
    # 운영에 받은 별점이 없어 지운다. 혹시 남아 있으면 지우지 않고 멈춘다
    has_rows = op.get_bind().scalar(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM service_feedbacks) "
            "OR EXISTS (SELECT 1 FROM user_suggestions WHERE source = 'RATING_POPUP')"
        )
    )
    if has_rows:
        raise RuntimeError("1차 배포 별점 · 팝업 의견이 남아 있어 지우지 않습니다.")
    op.drop_table("service_feedback_impressions")
    op.drop_constraint("ck_user_suggestions_source", "user_suggestions", type_="check")
    # 별점을 가리키던 FK · UNIQUE 도 칸과 같이 지워진다
    op.drop_column("user_suggestions", "service_feedback_id")
    op.create_check_constraint("ck_user_suggestions_source", "user_suggestions", MY_PAGE_ONLY)
    op.drop_index("ix_service_feedbacks_created", table_name="service_feedbacks")
    op.drop_table("service_feedbacks")


def downgrade() -> None:
    op.create_table(
        "service_feedbacks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("rating", sa.Integer(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="service_feedbacks_user_id_fkey",
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint("user_id", name="uq_service_feedbacks_user"),
        sa.CheckConstraint("rating BETWEEN 1 AND 5", name="ck_service_feedbacks_rating"),
    )
    op.create_index("ix_service_feedbacks_created", "service_feedbacks", ["created_at", "id"])
    op.add_column("user_suggestions", sa.Column("service_feedback_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "user_suggestions_service_feedback_id_fkey",
        "user_suggestions",
        "service_feedbacks",
        ["service_feedback_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_unique_constraint(
        "uq_user_suggestions_feedback", "user_suggestions", ["service_feedback_id"]
    )
    op.drop_constraint("ck_user_suggestions_source", "user_suggestions", type_="check")
    op.create_check_constraint("ck_user_suggestions_source", "user_suggestions", WITH_POPUP)
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
