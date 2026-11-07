"""대화 에이전트 저장소 테이블을 만든다.

Revision ID: c81300000001
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from alembic_git_revisions import get_down_revision
from sqlalchemy.dialects import postgresql

revision: str = "c81300000001"
down_revision: str | Sequence[str] | None = get_down_revision(revision)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _created_at() -> sa.Column:
    return sa.Column(
        "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )


def _updated_at() -> sa.Column:
    return sa.Column(
        "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )


def upgrade() -> None:
    op.create_table(
        "chat_conversations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("scope_pitch_id", sa.Uuid(), nullable=True),
        sa.Column("title", sa.String(length=100), nullable=False),
        sa.Column("goal_version", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "last_user_activity_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        _created_at(),
        _updated_at(),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["scope_pitch_id"], ["pitches.id"], ondelete="CASCADE"),
        sa.CheckConstraint("goal_version >= 0", name="check_chat_goal_version"),
    )
    op.create_index("ix_chat_conversations_user_id", "chat_conversations", ["user_id"])
    op.create_index(
        "ix_chat_conversations_scope_pitch_id", "chat_conversations", ["scope_pitch_id"]
    )

    op.create_table(
        "chat_messages",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=True),
        sa.Column("run_id", sa.Uuid(), nullable=True),
        sa.Column("client_request_id", sa.Uuid(), nullable=True),
        sa.Column("request_hash", sa.String(length=64), nullable=True),
        sa.Column("again_of_message_id", sa.Uuid(), nullable=True),
        sa.Column("claims", postgresql.JSONB(), nullable=True),
        sa.Column("cards", postgresql.JSONB(), nullable=True),
        sa.Column("assessments", postgresql.JSONB(), nullable=True),
        sa.Column("plan", postgresql.JSONB(), nullable=True),
        sa.Column("trace", postgresql.JSONB(), nullable=True),
        sa.Column("basis", postgresql.JSONB(), nullable=True),
        sa.Column("evidence", postgresql.JSONB(), nullable=True),
        sa.Column("sources", postgresql.JSONB(), nullable=True),
        sa.Column("usage", postgresql.JSONB(), nullable=True),
        sa.Column("meta", postgresql.JSONB(), nullable=True),
        sa.Column("source_pitch_ids", postgresql.JSONB(), nullable=True),
        sa.Column("source_take_ids", postgresql.JSONB(), nullable=True),
        _created_at(),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["conversation_id"], ["chat_conversations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["again_of_message_id"], ["chat_messages.id"]),
        sa.UniqueConstraint("conversation_id", "seq", name="uq_chat_messages_conversation_seq"),
        sa.UniqueConstraint(
            "user_id", "client_request_id", name="uq_chat_messages_user_client_request"
        ),
        sa.CheckConstraint(
            "role IN ('user', 'assistant', 'system')", name="check_chat_message_role"
        ),
        sa.CheckConstraint(
            "status IN ('DONE', 'PARTIAL', 'REFUSED')", name="check_chat_message_status"
        ),
        sa.CheckConstraint(
            "(client_request_id IS NULL) = (request_hash IS NULL)",
            name="check_chat_message_request_key",
        ),
    )
    op.create_index("ix_chat_messages_conversation_id", "chat_messages", ["conversation_id"])
    op.create_index("ix_chat_messages_user_id", "chat_messages", ["user_id"])

    op.create_table(
        "chat_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("message_id", sa.Uuid(), nullable=True),
        sa.Column("first_trigger", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("row_revision", sa.Integer(), server_default="0", nullable=False),
        sa.Column("state_revision", sa.Integer(), server_default="0", nullable=False),
        sa.Column("scope_pitch_id", sa.Uuid(), nullable=True),
        sa.Column("input", postgresql.JSONB(), nullable=False),
        sa.Column("state", postgresql.JSONB(), nullable=True),
        sa.Column("turn_calls", postgresql.JSONB(), nullable=True),
        sa.Column("progress_turn_call", sa.Integer(), server_default="0", nullable=False),
        sa.Column("progress_seq", sa.Integer(), server_default="0", nullable=False),
        sa.Column("plan", postgresql.JSONB(), nullable=True),
        sa.Column("trace", postgresql.JSONB(), nullable=True),
        sa.Column("interim_message", postgresql.JSONB(), nullable=True),
        sa.Column("budget_used", postgresql.JSONB(), nullable=True),
        sa.Column("usage", postgresql.JSONB(), nullable=True),
        sa.Column("read_sources", postgresql.JSONB(), nullable=True),
        sa.Column("error_code", sa.String(length=40), nullable=True),
        sa.Column("retry_after_s", sa.Integer(), nullable=True),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("approval_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approval_ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_owner", sa.String(length=100), nullable=True),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("fencing_token", sa.BigInteger(), server_default="0", nullable=False),
        _created_at(),
        _updated_at(),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["conversation_id"], ["chat_conversations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["message_id"], ["chat_messages.id"], ondelete="CASCADE"),
        sa.CheckConstraint(
            "first_trigger IN ('USER_MESSAGE', 'TAKE_REVIEWED')",
            name="check_chat_run_first_trigger",
        ),
        sa.CheckConstraint(
            "status IN ('QUEUED', 'RUNNING', 'WAITING_APPROVAL', 'DONE', 'PARTIAL', "
            "'REFUSED', 'FAILED', 'CANCELLED', 'EXPIRED')",
            name="check_chat_run_status",
        ),
        sa.CheckConstraint(
            "row_revision >= 0 AND state_revision >= 0", name="check_chat_run_revisions"
        ),
    )
    op.create_index("ix_chat_runs_conversation_id", "chat_runs", ["conversation_id"])
    op.create_index("ix_chat_runs_user_id", "chat_runs", ["user_id"])
    op.create_index("ix_chat_runs_finished_at", "chat_runs", ["finished_at"])
    op.create_index(
        "uq_chat_runs_one_active",
        "chat_runs",
        ["conversation_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('QUEUED', 'RUNNING', 'WAITING_APPROVAL')"),
    )

    op.create_table(
        "chat_actions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("action_id", sa.String(length=64), nullable=False),
        sa.Column("type", sa.String(length=20), nullable=False),
        sa.Column("args", postgresql.JSONB(), nullable=False),
        sa.Column("args_digest", sa.String(length=64), nullable=False),
        sa.Column("autonomy", sa.String(length=10), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("args_hash", sa.String(length=64), nullable=True),
        sa.Column("approved", sa.Boolean(), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=10), nullable=False),
        sa.Column("result", postgresql.JSONB(), nullable=True),
        sa.Column("source_take_ids", postgresql.JSONB(), nullable=True),
        sa.Column("undone_at", sa.DateTime(timezone=True), nullable=True),
        _created_at(),
        _updated_at(),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["conversation_id"], ["chat_conversations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint(
            "user_id", "type", "idempotency_key", name="uq_chat_actions_user_type_key"
        ),
        sa.UniqueConstraint("run_id", "action_id", name="uq_chat_actions_run_action"),
        sa.CheckConstraint(
            "type IN ('FETCH_PITCH', 'FETCH_TAKE', 'REASSESS', 'SAVE_GOAL', 'START_PRACTICE')",
            name="check_chat_action_type",
        ),
        sa.CheckConstraint(
            "autonomy IN ('AUTO', 'AUTO_UNDO', 'ASK')", name="check_chat_action_autonomy"
        ),
        sa.CheckConstraint(
            "status IN ('PENDING', 'RUNNING', 'SUCCEEDED', 'FAILED')",
            name="check_chat_action_status",
        ),
    )
    op.create_index("ix_chat_actions_conversation_id", "chat_actions", ["conversation_id"])
    op.create_index("ix_chat_actions_user_id", "chat_actions", ["user_id"])

    op.create_table(
        "chat_goals",
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("goal", postgresql.JSONB(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=True),
        sa.Column("base_take_id", sa.Uuid(), nullable=True),
        sa.Column("action_id", sa.Uuid(), nullable=True),
        _created_at(),
        sa.PrimaryKeyConstraint("conversation_id", "version"),
        sa.ForeignKeyConstraint(["conversation_id"], ["chat_conversations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["action_id"], ["chat_actions.id"], ondelete="SET NULL"),
        sa.CheckConstraint(
            "status IN ('OPEN', 'ACHIEVED', 'NOT_ACHIEVED', 'BASELINE_MISMATCH')",
            name="check_chat_goal_status",
        ),
        sa.CheckConstraint("(goal IS NULL) = (status IS NULL)", name="check_chat_goal_status_set"),
    )
    op.create_index("ix_chat_goals_base_take_id", "chat_goals", ["base_take_id"])

    op.create_table(
        "chat_daily_usage",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("turns", sa.Integer(), server_default="0", nullable=False),
        sa.Column("reassessments", sa.Integer(), server_default="0", nullable=False),
        sa.Column("goal_checks", sa.Integer(), server_default="0", nullable=False),
        sa.Column("reserved", sa.Integer(), server_default="0", nullable=False),
        sa.Column("sent", sa.Integer(), server_default="0", nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint(
            "user_id",
            "day",
            name="uq_chat_daily_usage_user_day",
            postgresql_nulls_not_distinct=True,
        ),
        sa.CheckConstraint(
            "turns >= 0 AND reassessments >= 0 AND goal_checks >= 0 "
            "AND reserved >= 0 AND sent >= 0",
            name="check_chat_daily_usage_counts",
        ),
        sa.CheckConstraint(
            "(user_id IS NULL AND turns = 0 AND reassessments = 0 AND goal_checks = 0) "
            "OR (user_id IS NOT NULL AND reserved = 0 AND sent = 0)",
            name="check_chat_daily_usage_scope",
        ),
    )
    op.create_index("ix_chat_daily_usage_day", "chat_daily_usage", ["day"])

    op.create_table(
        "chat_leases",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=True),
        sa.Column("goal_version", sa.Integer(), nullable=True),
        sa.Column("take_id", sa.Uuid(), nullable=True),
        sa.Column("review_revision", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=10), nullable=True),
        sa.Column("run_id", sa.Uuid(), nullable=True),
        sa.Column("owner", sa.String(length=100), nullable=True),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("fencing_token", sa.BigInteger(), server_default="0", nullable=False),
        _created_at(),
        _updated_at(),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["conversation_id"], ["chat_conversations.id"], ondelete="CASCADE"),
        sa.UniqueConstraint(
            "conversation_id",
            "goal_version",
            "take_id",
            "review_revision",
            name="uq_chat_leases_goal_check",
        ),
        sa.CheckConstraint("kind IN ('GOAL_CHECK', 'CLEANUP')", name="check_chat_lease_kind"),
        sa.CheckConstraint("status IN ('PENDING', 'DONE')", name="check_chat_lease_status"),
        sa.CheckConstraint(
            "(kind = 'GOAL_CHECK') = (conversation_id IS NOT NULL AND goal_version IS NOT NULL "
            "AND take_id IS NOT NULL AND review_revision IS NOT NULL AND status IS NOT NULL)",
            name="check_chat_lease_goal_key",
        ),
    )
    op.create_index("ix_chat_leases_take_id", "chat_leases", ["take_id"])
    op.create_index("ix_chat_leases_lease_until", "chat_leases", ["lease_until"])
    op.create_index(
        "uq_chat_leases_one_cleanup",
        "chat_leases",
        ["kind"],
        unique=True,
        postgresql_where=sa.text("kind = 'CLEANUP'"),
    )

    op.create_table(
        "chat_run_logs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("ms", sa.Integer(), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        _created_at(),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("run_id"),
    )
    op.create_index("ix_chat_run_logs_day", "chat_run_logs", ["day"])
    op.create_index("ix_chat_run_logs_user_id", "chat_run_logs", ["user_id"])

    op.create_table(
        "chat_diagnostics",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("turn_call", sa.Integer(), nullable=False),
        sa.Column("request_id", sa.String(length=64), nullable=False),
        sa.Column("diagnostics", postgresql.JSONB(), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["conversation_id"], ["chat_conversations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("run_id", "turn_call", name="uq_chat_diagnostics_run_call"),
    )
    op.create_index("ix_chat_diagnostics_conversation_id", "chat_diagnostics", ["conversation_id"])
    op.create_index("ix_chat_diagnostics_user_id", "chat_diagnostics", ["user_id"])


def downgrade() -> None:
    op.drop_table("chat_diagnostics")
    op.drop_table("chat_run_logs")
    op.drop_table("chat_leases")
    op.drop_table("chat_daily_usage")
    op.drop_table("chat_goals")
    op.drop_table("chat_actions")
    op.drop_table("chat_runs")
    op.drop_table("chat_messages")
    op.drop_table("chat_conversations")
