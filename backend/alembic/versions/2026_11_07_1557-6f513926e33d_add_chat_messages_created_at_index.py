"""최신 자료 판을 고르는 쿼리가 표 전체를 훑지 않게 chat_messages.created_at 색인을 더한다.

Revision ID: 6f513926e33d
"""

from collections.abc import Sequence

from alembic import op
from alembic_git_revisions import get_down_revision

revision: str = "6f513926e33d"
down_revision: str | Sequence[str] | None = get_down_revision(revision)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index("ix_chat_messages_created_at", "chat_messages", ["created_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_chat_messages_created_at", table_name="chat_messages")
