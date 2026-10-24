"""add take_metric_seconds

Revision ID: f0407b32a13e
Revises: 2166f2e740d7
Create Date: 2026-10-23 18:05:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from alembic_git_revisions import get_down_revision
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'f0407b32a13e'
down_revision: str | Sequence[str] | None = get_down_revision(revision)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # 발표 중 1초 원자료 (WS metrics). 같은 1초는 (take_id, t_ms) 로 한 번만 남는다
    op.create_table('take_metric_seconds',
    sa.Column('take_id', sa.Uuid(), nullable=False),
    sa.Column('t_ms', sa.Integer(), nullable=False),
    sa.Column('slide_number', sa.Integer(), nullable=True),
    sa.Column('gaze', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('voice', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('stt_status', sa.String(length=16), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint(
        "stt_status IN ('connecting', 'ok', 'reconnecting', 'degraded', 'closed')",
        name='check_take_metric_seconds_stt_status',
    ),
    sa.ForeignKeyConstraint(['take_id'], ['takes.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('take_id', 't_ms')
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('take_metric_seconds')
