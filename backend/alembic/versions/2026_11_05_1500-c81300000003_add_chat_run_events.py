"""대화 실행의 진행 줄과 답 문장 줄을 순서대로 쌓는다.

Revision ID: c81300000003
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from alembic_git_revisions import get_down_revision
from sqlalchemy.dialects import postgresql

revision: str = "c81300000003"
down_revision: str | Sequence[str] | None = get_down_revision(revision)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "chat_run_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("event_seq", sa.Integer(), nullable=False),
        sa.Column("event", sa.String(length=10), nullable=False),
        sa.Column("data", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["run_id"], ["chat_runs.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("run_id", "event_seq", name="uq_chat_run_events_run_seq"),
        sa.CheckConstraint(
            "event IN ('plan', 'tool_done', 'claim')", name="check_chat_run_event_event"
        ),
    )


def downgrade() -> None:
    op.drop_table("chat_run_events")
