"""rename file_url to file_key and add script_versions file_key

Revision ID: e5cdee650e92
Revises: 6589fdfc2f9f
Create Date: 2026-09-19 15:55:49.937820

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'e5cdee650e92'
down_revision: str | Sequence[str] | None = '6589fdfc2f9f'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.alter_column(
        'presentation_versions',
        'file_url',
        new_column_name='file_key',
        existing_type=sa.String(length=255),
        existing_nullable=False,
    )
    op.add_column(
        'script_versions',
        sa.Column('file_key', sa.String(length=255), nullable=False),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('script_versions', 'file_key')
    op.alter_column(
        'presentation_versions',
        'file_key',
        new_column_name='file_url',
        existing_type=sa.String(length=255),
        existing_nullable=False,
    )
