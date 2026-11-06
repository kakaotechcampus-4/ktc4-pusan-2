"""읽고 쓰는 곳이 없는 live_feedbacks 테이블을 지운다. 코치가 한 말은 coach_events 에만 둔다.

Revision ID: 2f43f399e6ac
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from alembic_git_revisions import get_down_revision

revision: str = "2f43f399e6ac"
down_revision: str | Sequence[str] | None = get_down_revision(revision)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_index(op.f("ix_live_feedbacks_take_id"), table_name="live_feedbacks")
    op.drop_table("live_feedbacks")


def downgrade() -> None:
    op.create_table(
        "live_feedbacks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("take_id", sa.Uuid(), nullable=False),
        sa.Column("type", sa.String(length=20), nullable=False),
        sa.Column("message", sa.String(length=255), nullable=False),
        sa.Column("triggered_at_ms", sa.Integer(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.ForeignKeyConstraint(["take_id"], ["takes.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_live_feedbacks_take_id"), "live_feedbacks", ["take_id"], unique=False)
