"""merge oauth unique and pitch take branches

Revision ID: 6589fdfc2f9f
Revises: 089e4bdb0e9e, 09170142665e
Create Date: 2026-09-18 14:15:41.712152

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = '6589fdfc2f9f'
down_revision: str | Sequence[str] | None = ('089e4bdb0e9e', '09170142665e')
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
