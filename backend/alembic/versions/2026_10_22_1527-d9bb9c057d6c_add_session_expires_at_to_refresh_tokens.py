"""add session_expires_at to refresh_tokens

Revision ID: d9bb9c057d6c
Revises: d82be488a00c
Create Date: 2026-10-22 15:27:03.677723

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from alembic_git_revisions import get_down_revision


# revision identifiers, used by Alembic.
revision: str = 'd9bb9c057d6c'
down_revision: str | Sequence[str] | None = get_down_revision(revision)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'refresh_tokens',
        sa.Column('session_expires_at', sa.DateTime(timezone=True), nullable=True),
    )
    # 이미 있는 토큰은 그 기기의 첫 토큰이 만들어진 시각(= 로그인 시각) + 30일로 채운다.
    # 만료도 그보다 늦지 않게 당긴다. 이미 30일이 지난 세션은 다음 갱신에서 끝난다
    op.execute(
        """
        UPDATE refresh_tokens AS t
        SET session_expires_at = s.started_at + interval '30 days'
        FROM (
            SELECT device_id, min(created_at) AS started_at
            FROM refresh_tokens
            GROUP BY device_id
        ) AS s
        WHERE t.device_id = s.device_id
        """
    )
    op.execute(
        "UPDATE refresh_tokens SET expires_at = session_expires_at"
        " WHERE expires_at > session_expires_at"
    )
    op.alter_column('refresh_tokens', 'session_expires_at', nullable=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('refresh_tokens', 'session_expires_at')
