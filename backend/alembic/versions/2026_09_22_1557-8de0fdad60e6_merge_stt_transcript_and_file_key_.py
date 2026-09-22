"""merge stt transcript and file key branches

Revision ID: 8de0fdad60e6
Revises: 982e8a605f53, e5cdee650e92
Create Date: 2026-09-22 15:57:26.607441

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = '8de0fdad60e6'
down_revision: str | Sequence[str] | None = ('982e8a605f53', 'e5cdee650e92')
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
