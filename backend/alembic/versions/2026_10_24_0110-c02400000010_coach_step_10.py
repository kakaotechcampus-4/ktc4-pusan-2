"""코치 연동 10단계 저장 칸. 해당 기능과 함께 적용한다."""

import sqlalchemy as sa
from alembic import op
from alembic_git_revisions import get_down_revision
from sqlalchemy.dialects import postgresql

revision = "c02400000010"
down_revision = get_down_revision(revision)
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "coaching_plans",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("pitch_id", sa.Uuid(), nullable=False),
        sa.Column("source_take_id", sa.Uuid(), nullable=True),
        sa.Column("script_version_id", sa.Uuid(), nullable=True),
        sa.Column("target_ms", sa.Integer(), nullable=True),
        sa.Column("min_ms", sa.Integer(), nullable=True),
        sa.Column("max_ms", sa.Integer(), nullable=True),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("meta", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.ForeignKeyConstraint(["pitch_id"], ["pitches.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_coaching_plans_pitch_requested",
        "coaching_plans",
        ["pitch_id", "requested_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_coaching_plans_pitch_requested", table_name="coaching_plans")
    op.drop_table("coaching_plans")
