"""add script coverage columns

Revision ID: dbd0c687f1aa
Revises: f0407b32a13e
Create Date: 2026-10-24 15:28:28.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'dbd0c687f1aa'
down_revision: str | Sequence[str] | None = 'c02400000010'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('takes', sa.Column('coverage_retry_used', sa.Boolean(), server_default='false', nullable=False))
    op.add_column('takes', sa.Column('coverage_rescore_retry_used', sa.Boolean(), server_default='false', nullable=False))
    op.add_column('takes', sa.Column('analysis_hold_reason', sa.String(length=32), nullable=True))
    op.create_check_constraint('check_take_analysis_hold_reason', 'takes',
        "analysis_hold_reason IN ('COVERAGE_RESCORE_FAILED')")

    op.add_column('script_versions', sa.Column('rubric_status', sa.String(length=16), nullable=True))
    op.add_column('script_versions', sa.Column('rubric_requested_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('script_versions', sa.Column('rubric_retry_used', sa.Boolean(), server_default='false', nullable=False))
    op.create_check_constraint('ck_script_versions_rubric_status', 'script_versions',
        "rubric_status IN ('PENDING', 'DONE', 'PARTIAL', 'FAILED')")

    op.add_column('script_slides', sa.Column('rubric', postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.add_column('script_slides', sa.Column('rubric_meta', postgresql.JSONB(astext_type=sa.Text()), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('script_slides', 'rubric_meta')
    op.drop_column('script_slides', 'rubric')

    op.drop_constraint('ck_script_versions_rubric_status', 'script_versions', type_='check')
    op.drop_column('script_versions', 'rubric_retry_used')
    op.drop_column('script_versions', 'rubric_requested_at')
    op.drop_column('script_versions', 'rubric_status')

    op.drop_constraint('check_take_analysis_hold_reason', 'takes', type_='check')
    op.drop_column('takes', 'analysis_hold_reason')
    op.drop_column('takes', 'coverage_rescore_retry_used')
    op.drop_column('takes', 'coverage_retry_used')
