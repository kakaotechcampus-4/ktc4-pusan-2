"""코치 연동 6단계 저장 칸. 해당 기능과 함께 적용한다."""

import sqlalchemy as sa
from alembic import op
from alembic_git_revisions import get_down_revision
from sqlalchemy.dialects import postgresql

revision = "c02400000006"
down_revision = get_down_revision(revision)
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "coach_events",
        sa.Column("take_id", sa.Uuid(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("event_id", sa.Text(), nullable=False),
        sa.Column("t_ms", sa.Integer(), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("meta", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["take_id"], ["takes.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("take_id", "seq"),
        sa.UniqueConstraint("take_id", "event_id", name="uq_coach_events_take_event"),
    )
    op.alter_column("take_summaries", "average_wpm", new_column_name="average_cpm")


def downgrade() -> None:
    op.alter_column("take_summaries", "average_cpm", new_column_name="average_wpm")
    op.drop_table("coach_events")
