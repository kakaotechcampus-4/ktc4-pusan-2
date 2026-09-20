"""create take_transcript_segments

Revision ID: 982e8a605f53
Revises: 09170142665e
Create Date: 2026-09-19 15:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '982e8a605f53'
down_revision: str | Sequence[str] | None = '09170142665e'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # 실시간 STT 가 확정(is_final)한 전사 구간. Take 당 N 행, 리포트의 유일한 원천.
    op.create_table('take_transcript_segments',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('take_id', sa.Uuid(), nullable=False),
    sa.Column('seq', sa.Integer(), nullable=False),
    sa.Column('stt_session_no', sa.Integer(), nullable=False),
    sa.Column('start_ms', sa.Integer(), nullable=False),
    sa.Column('end_ms', sa.Integer(), nullable=False),
    sa.Column('transcript', sa.Text(), nullable=False),
    sa.Column('words', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('confidence', sa.Float(), nullable=False),
    sa.Column('speech_final', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['take_id'], ['takes.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('take_id', 'seq', name='uq_take_transcript_segments_take_seq')
    )
    op.create_index(op.f('ix_take_transcript_segments_take_id'), 'take_transcript_segments', ['take_id'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_take_transcript_segments_take_id'), table_name='take_transcript_segments')
    op.drop_table('take_transcript_segments')
