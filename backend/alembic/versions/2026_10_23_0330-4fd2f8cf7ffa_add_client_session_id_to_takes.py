"""add client_session_id to takes

Revision ID: 4fd2f8cf7ffa
Revises: 8437662db331
Create Date: 2026-10-23 03:30:25.243750

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from alembic_git_revisions import get_down_revision


# revision identifiers, used by Alembic.
revision: str = '4fd2f8cf7ffa'
down_revision: str | Sequence[str] | None = get_down_revision(revision)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # Take 생성 요청의 멱등 키. 이 칸이 생기기 전에 만든 Take 는 비어 있다
    op.add_column('takes', sa.Column('client_session_id', sa.Uuid(), nullable=True))
    op.create_unique_constraint(
        'uq_takes_pitch_client_session', 'takes', ['pitch_id', 'client_session_id']
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint('uq_takes_pitch_client_session', 'takes', type_='unique')
    op.drop_column('takes', 'client_session_id')
