"""리포트마다 남기는 Take별 설문을 저장한다.

Revision ID: b77600000003
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from alembic_git_revisions import get_down_revision

revision: str = "b77600000003"
down_revision: str | Sequence[str] | None = get_down_revision(revision)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "take_surveys",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("take_id", sa.Uuid(), nullable=True),
        sa.Column("accuracy_rating", sa.Integer(), nullable=False),
        sa.Column("usefulness_rating", sa.Integer(), nullable=False),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("question_version", sa.Integer(), server_default="1", nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_take_surveys_user", ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["take_id"], ["takes.id"], name="fk_take_surveys_take", ondelete="SET NULL"
        ),
        sa.UniqueConstraint("take_id", name="uq_take_surveys_take"),
        sa.CheckConstraint("accuracy_rating BETWEEN 1 AND 5", name="ck_take_surveys_accuracy"),
        sa.CheckConstraint("usefulness_rating BETWEEN 1 AND 5", name="ck_take_surveys_usefulness"),
        sa.CheckConstraint(
            "comment IS NULL OR char_length(comment) BETWEEN 1 AND 2000",
            name="ck_take_surveys_comment",
        ),
        sa.CheckConstraint("question_version >= 1", name="ck_take_surveys_question_version"),
    )
    op.create_index("ix_take_surveys_user", "take_surveys", ["user_id"])
    op.create_index("ix_take_surveys_created", "take_surveys", ["created_at", "id"])


def downgrade() -> None:
    op.drop_index("ix_take_surveys_created", table_name="take_surveys")
    op.drop_index("ix_take_surveys_user", table_name="take_surveys")
    op.drop_table("take_surveys")
