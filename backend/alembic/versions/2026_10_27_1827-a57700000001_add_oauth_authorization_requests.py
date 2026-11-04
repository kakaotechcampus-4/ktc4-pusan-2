"""구글 로그인의 임시 인증 요청을 Redis 대신 PostgreSQL 에 둔다.

Redis 에 남아 있던 진행 중 요청은 옮기지 않는다. 배포 중에 로그인하던 사용자는
다시 로그인하면 된다 (backend/README.md 인증 절).

Revision ID: a57700000001
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from alembic_git_revisions import get_down_revision

revision: str = "a57700000001"
down_revision: str | Sequence[str] | None = get_down_revision(revision)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "oauth_authorization_requests",
        sa.Column("state", sa.String(length=64), nullable=False),
        sa.Column("nonce", sa.String(length=64), nullable=False),
        sa.Column("code_verifier", sa.String(length=128), nullable=False),
        sa.Column("browser_token", sa.String(length=64), nullable=False),
        sa.Column("return_to", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("state"),
    )
    op.create_index(
        "ix_oauth_authorization_requests_expires_at",
        "oauth_authorization_requests",
        ["expires_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_oauth_authorization_requests_expires_at", table_name="oauth_authorization_requests"
    )
    op.drop_table("oauth_authorization_requests")
