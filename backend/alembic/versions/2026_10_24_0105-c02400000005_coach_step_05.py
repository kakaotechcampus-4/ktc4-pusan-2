"""코치 연동 5단계 저장 칸. 해당 기능과 함께 적용한다."""

import sqlalchemy as sa
from alembic import op
from alembic_git_revisions import get_down_revision
from sqlalchemy.dialects import postgresql

revision = "c02400000005"
down_revision = get_down_revision(revision)
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "take_slide_events",
        sa.Column("take_id", sa.Uuid(), nullable=False),
        sa.Column("started_ms", sa.Integer(), nullable=False),
        sa.Column("slide_number", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["take_id"], ["takes.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("take_id", "started_ms"),
    )
    op.add_column("take_transcript_segments", sa.Column("final_at_ms", sa.Integer(), nullable=True))
    op.add_column(
        "takes", sa.Column("utterance_ends", postgresql.JSONB(astext_type=sa.Text()), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("takes", "utterance_ends")
    op.drop_column("take_transcript_segments", "final_at_ms")
    op.drop_table("take_slide_events")
