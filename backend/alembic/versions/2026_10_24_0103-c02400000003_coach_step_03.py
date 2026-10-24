"""코치 연동 3단계 저장 칸. 해당 기능과 함께 적용한다."""

import sqlalchemy as sa
from alembic import op
from alembic_git_revisions import get_down_revision

revision = "c02400000003"
down_revision = get_down_revision(revision)
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "take_ai_results", sa.Column("revision", sa.Integer(), server_default="0", nullable=False)
    )
    op.alter_column(
        "take_ai_results",
        "kind",
        existing_type=sa.VARCHAR(length=20),
        type_=sa.String(length=32),
        existing_nullable=False,
    )
    op.add_column("takes", sa.Column("standard_version", sa.Integer(), nullable=True))
    op.add_column("takes", sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("takes", "completed_at")
    op.drop_column("takes", "standard_version")
    op.alter_column(
        "take_ai_results",
        "kind",
        existing_type=sa.String(length=32),
        type_=sa.VARCHAR(length=20),
        existing_nullable=False,
    )
    op.drop_column("take_ai_results", "revision")
