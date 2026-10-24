"""코치 연동 4단계 저장 칸. 해당 기능과 함께 적용한다."""

import sqlalchemy as sa
from alembic import op
from alembic_git_revisions import get_down_revision

revision = "c02400000004"
down_revision = get_down_revision(revision)
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "takes", sa.Column("raw_data_consent", sa.Boolean(), server_default="false", nullable=False)
    )
    op.add_column(
        "users", sa.Column("raw_data_consent", sa.Boolean(), server_default="false", nullable=False)
    )


def downgrade() -> None:
    op.drop_column("users", "raw_data_consent")
    op.drop_column("takes", "raw_data_consent")
