"""서비스 평점·의견·표시 승인 기록을 저장한다."""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from alembic_git_revisions import get_down_revision

revision: str = "f54000000001"
down_revision: str | Sequence[str] | None = get_down_revision(revision)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "service_feedbacks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("rating", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("user_id", name="uq_service_feedbacks_user"),
        sa.CheckConstraint("rating BETWEEN 1 AND 5", name="ck_service_feedbacks_rating"),
    )
    op.create_index("ix_service_feedbacks_created", "service_feedbacks", ["created_at", "id"])
    op.create_table(
        "user_suggestions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("service_feedback_id", sa.Uuid(), nullable=True),
        sa.Column("request_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_user_suggestions_user", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["service_feedback_id"], ["service_feedbacks.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("service_feedback_id", name="uq_user_suggestions_feedback"),
        sa.UniqueConstraint("user_id", "request_id", name="uq_user_suggestions_request"),
        sa.CheckConstraint("char_length(content) BETWEEN 1 AND 2000", name="ck_user_suggestions_content"),
        sa.CheckConstraint(
            "(source = 'RATING_POPUP' AND service_feedback_id IS NOT NULL AND request_id IS NULL) "
            "OR (source = 'MY_PAGE' AND service_feedback_id IS NULL AND request_id IS NOT NULL)",
            name="ck_user_suggestions_source",
        ),
    )
    op.create_index("ix_user_suggestions_created", "user_suggestions", ["created_at", "id"])
    op.create_table(
        "service_feedback_impressions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("display_request_id", sa.Uuid(), nullable=False),
        sa.Column("display_number", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("user_id", "display_request_id", name="uq_service_feedback_impressions_request"),
        sa.UniqueConstraint("user_id", "display_number", name="uq_service_feedback_impressions_number"),
        sa.CheckConstraint("display_number BETWEEN 1 AND 3", name="ck_service_feedback_impressions_number"),
    )


def downgrade() -> None:
    op.drop_table("service_feedback_impressions")
    op.drop_index("ix_user_suggestions_created", table_name="user_suggestions")
    op.drop_table("user_suggestions")
    op.drop_index("ix_service_feedbacks_created", table_name="service_feedbacks")
    op.drop_table("service_feedbacks")
