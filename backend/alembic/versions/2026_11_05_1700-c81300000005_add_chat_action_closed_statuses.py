"""대화 행동 상태에 거절 · 만료 · 취소를 더한다.

Revision ID: c81300000005
"""

from collections.abc import Sequence

from alembic import op
from alembic_git_revisions import get_down_revision

revision: str = "c81300000005"
down_revision: str | Sequence[str] | None = get_down_revision(revision)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("check_chat_action_status", "chat_actions", type_="check")
    op.create_check_constraint(
        "check_chat_action_status",
        "chat_actions",
        "status IN ('PENDING', 'RUNNING', 'SUCCEEDED', 'FAILED', 'REJECTED', 'EXPIRED', "
        "'CANCELLED')",
    )


def downgrade() -> None:
    op.drop_constraint("check_chat_action_status", "chat_actions", type_="check")
    op.create_check_constraint(
        "check_chat_action_status",
        "chat_actions",
        "status IN ('PENDING', 'RUNNING', 'SUCCEEDED', 'FAILED')",
    )
