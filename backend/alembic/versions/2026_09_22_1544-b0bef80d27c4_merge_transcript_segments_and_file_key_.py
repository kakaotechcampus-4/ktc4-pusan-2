"""merge transcript segments and file_key branches

Revision ID: b0bef80d27c4
Revises: 982e8a605f53, e5cdee650e92
Create Date: 2026-09-22 15:44:49.158876

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'b0bef80d27c4'
down_revision: str | Sequence[str] | None = ('982e8a605f53', 'e5cdee650e92')
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
