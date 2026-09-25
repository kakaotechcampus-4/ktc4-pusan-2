"""drop pitches best_take_id FK and standard_id column

Revision ID: 15398e35128d
Revises: 2bf200c5e4c0
Create Date: 2026-09-25 12:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = '15398e35128d'
down_revision: str | Sequence[str] | None = '2bf200c5e4c0'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.drop_constraint('pitches_best_take_id_fkey', 'pitches', type_='foreignkey')
    op.drop_column('pitches', 'standard_id')


def downgrade() -> None:
    """Downgrade schema."""
    op.add_column('pitches', sa.Column('standard_id', sa.Uuid(), nullable=True))
    op.create_foreign_key(
        'pitches_best_take_id_fkey', 'pitches', 'takes', ['best_take_id'], ['id']
    )
