"""add script coverage columns

Revision ID: dbd0c687f1aa
Revises: f0407b32a13e
Create Date: 2026-10-24 15:28:28.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from alembic_git_revisions import get_down_revision
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'dbd0c687f1aa'
down_revision: str | Sequence[str] | None = get_down_revision(revision)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('takes', sa.Column('coverage_retry_used', sa.Boolean(), server_default='false', nullable=False))
    op.add_column('takes', sa.Column('coverage_rescore_retry_used', sa.Boolean(), server_default='false', nullable=False))
    op.add_column('takes', sa.Column('analysis_hold_reason', sa.String(length=32), nullable=True))
    op.create_check_constraint('check_take_analysis_hold_reason', 'takes',
        "analysis_hold_reason IN ('COVERAGE_RESCORE_FAILED')")

    op.alter_column('take_ai_results', 'kind',
        existing_type=sa.String(length=20),
        type_=sa.String(length=32),
        existing_nullable=False)
    op.add_column('take_ai_results', sa.Column('revision', sa.Integer(), server_default='0', nullable=False))

    op.add_column('script_versions', sa.Column('rubric_status', sa.String(length=16), nullable=True))
    op.add_column('script_versions', sa.Column('rubric_requested_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('script_versions', sa.Column('rubric_retry_used', sa.Boolean(), server_default='false', nullable=False))
    op.create_check_constraint('ck_script_versions_rubric_status', 'script_versions',
        "rubric_status IN ('PENDING', 'DONE', 'PARTIAL', 'FAILED')")

    op.add_column('script_slides', sa.Column('rubric', postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.add_column('script_slides', sa.Column('rubric_meta', postgresql.JSONB(astext_type=sa.Text()), nullable=True))

    op.create_table('take_slide_events',
    sa.Column('take_id', sa.Uuid(), nullable=False),
    sa.Column('started_ms', sa.Integer(), nullable=False),
    sa.Column('slide_number', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['take_id'], ['takes.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('take_id', 'started_ms')
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('take_slide_events')

    op.drop_column('script_slides', 'rubric_meta')
    op.drop_column('script_slides', 'rubric')

    op.drop_constraint('ck_script_versions_rubric_status', 'script_versions', type_='check')
    op.drop_column('script_versions', 'rubric_retry_used')
    op.drop_column('script_versions', 'rubric_requested_at')
    op.drop_column('script_versions', 'rubric_status')

    op.drop_column('take_ai_results', 'revision')
    op.alter_column('take_ai_results', 'kind',
        existing_type=sa.String(length=32),
        type_=sa.String(length=20),
        existing_nullable=False)

    op.drop_constraint('check_take_analysis_hold_reason', 'takes', type_='check')
    op.drop_column('takes', 'analysis_hold_reason')
    op.drop_column('takes', 'coverage_rescore_retry_used')
    op.drop_column('takes', 'coverage_retry_used')
