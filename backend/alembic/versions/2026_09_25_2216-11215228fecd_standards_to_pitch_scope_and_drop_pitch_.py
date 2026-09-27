"""standards to pitch scope and drop pitch best_take_id standard_id

Revision ID: 11215228fecd
Revises: 38d6a6f320cb
Create Date: 2026-09-25 22:16:23.515445

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = '11215228fecd'
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
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['pitch_id'], ['pitches.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_standards_pitch_id'), 'standards', ['pitch_id'], unique=False)

    op.drop_constraint('pitches_best_take_id_fkey', 'pitches', type_='foreignkey')
    op.drop_column('pitches', 'best_take_id')
    op.drop_column('pitches', 'standard_id')


def downgrade() -> None:
    """Downgrade schema."""
    op.add_column('pitches', sa.Column('standard_id', sa.Uuid(), nullable=True))
    op.add_column('pitches', sa.Column('best_take_id', sa.Uuid(), nullable=True))
    op.create_foreign_key(
        'pitches_best_take_id_fkey', 'pitches', 'takes', ['best_take_id'], ['id']
    )

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
