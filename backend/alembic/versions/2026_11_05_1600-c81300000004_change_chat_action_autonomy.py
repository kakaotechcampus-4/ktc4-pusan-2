"""대화 행동 등급을 AUTO · ASK · BUTTON 으로 바꾼다.

Revision ID: c81300000004
"""

from collections.abc import Sequence

from alembic import op
from alembic_git_revisions import get_down_revision

revision: str = "c81300000004"
down_revision: str | Sequence[str] | None = get_down_revision(revision)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("check_chat_action_autonomy", "chat_actions", type_="check")
    op.create_check_constraint(
        "check_chat_action_autonomy", "chat_actions", "autonomy IN ('AUTO', 'ASK', 'BUTTON')"
    )


def downgrade() -> None:
    op.drop_constraint("check_chat_action_autonomy", "chat_actions", type_="check")
    op.create_check_constraint(
        "check_chat_action_autonomy", "chat_actions", "autonomy IN ('AUTO', 'AUTO_UNDO', 'ASK')"
    )
