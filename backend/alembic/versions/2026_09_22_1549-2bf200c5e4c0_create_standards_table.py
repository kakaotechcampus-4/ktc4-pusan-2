"""standards 를 pitch 소속으로 바꾸고 goal_time_sec 를 채운다

Revision ID: 2bf200c5e4c0
Revises: 38d6a6f320cb
Create Date: 2026-09-22 15:49:55.808222

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = '2bf200c5e4c0'
down_revision: str | Sequence[str] | None = '38d6a6f320cb'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.drop_index(op.f('ix_standards_user_id'), table_name='standards')
    op.drop_table('standards')

    op.create_table('standards',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('pitch_id', sa.Uuid(), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('title', sa.String(length=50), nullable=False),
    sa.Column('create_at', sa.Date(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['pitch_id'], ['pitches.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('pitch_id', 'version', name='uq_standards_pitch_version')
    )
    op.create_index(op.f('ix_standards_pitch_id'), 'standards', ['pitch_id'], unique=False)

    op.execute(
        "UPDATE takes SET goal_time_sec = pitches.time_limit_sec "
        "FROM pitches WHERE pitches.id = takes.pitch_id AND takes.goal_time_sec IS NULL"
    )
    op.alter_column('takes', 'goal_time_sec', nullable=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.alter_column('takes', 'goal_time_sec', nullable=True)

    op.drop_index(op.f('ix_standards_pitch_id'), table_name='standards')
    op.drop_table('standards')

    op.create_table('standards',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('title', sa.String(length=50), nullable=False),
    sa.Column('create_at', sa.Date(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_standards_user_id'), 'standards', ['user_id'], unique=False)
