"""대화 기억 설정 · 코칭 프로필 항목 · 대화 요약과 '기억 없이' 대화 표시를 더한다.

Revision ID: c81300000006
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from alembic_git_revisions import get_down_revision
from sqlalchemy.dialects import postgresql

revision: str = "c81300000006"
down_revision: str | Sequence[str] | None = get_down_revision(revision)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "chat_memory_settings",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("memory_version", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("memory_version >= 0", name="check_chat_memory_version"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id"),
    )
    op.create_table(
        "chat_profile_items",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("key", sa.String(length=20), nullable=False),
        sa.Column("value", sa.String(length=80), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "key IN ('TONE', 'EXPLAIN_STYLE', 'FOCUS_AREA', 'CONTEXT')",
            name="check_chat_profile_item_key",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "key", name="uq_chat_profile_items_user_key"),
    )
    op.create_table(
        "chat_summaries",
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("items", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("summary_version", sa.Integer(), server_default="0", nullable=False),
        sa.Column("covered_message_id", sa.Uuid(), nullable=False),
        sa.Column("source_pitch_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("source_take_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("summary_version >= 0", name="check_chat_summary_version"),
        sa.ForeignKeyConstraint(["conversation_id"], ["chat_conversations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["covered_message_id"], ["chat_messages.id"]),
        sa.PrimaryKeyConstraint("conversation_id"),
    )
    op.add_column(
        "chat_conversations",
        sa.Column("without_memory", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )


def downgrade() -> None:
    op.drop_column("chat_conversations", "without_memory")
    op.drop_table("chat_summaries")
    op.drop_table("chat_profile_items")
    op.drop_table("chat_memory_settings")
