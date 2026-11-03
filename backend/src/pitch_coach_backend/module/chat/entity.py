import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from pitch_coach_backend.core.database import (
    Base,
    CreatedAtMixin,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
)


class ChatConversation(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "chat_conversations"
    __table_args__ = (CheckConstraint("goal_version >= 0", name="check_chat_goal_version"),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    scope_pitch_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("pitches.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(String(100), nullable=False)
    goal_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    last_user_activity_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ChatGoal(CreatedAtMixin, Base):
    __tablename__ = "chat_goals"
    __table_args__ = (
        CheckConstraint(
            "status IN ('OPEN', 'ACHIEVED', 'NOT_ACHIEVED', 'BASELINE_MISMATCH')",
            name="check_chat_goal_status",
        ),
        CheckConstraint("(goal IS NULL) = (status IS NULL)", name="check_chat_goal_status_set"),
    )

    conversation_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("chat_conversations.id", ondelete="CASCADE"), primary_key=True
    )
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    goal: Mapped[Any | None] = mapped_column(JSONB)
    status: Mapped[str | None] = mapped_column(String(20))
    base_take_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, index=True)
    action_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("chat_actions.id", ondelete="SET NULL")
    )


class ChatMessage(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "chat_messages"
    __table_args__ = (
        UniqueConstraint("conversation_id", "seq", name="uq_chat_messages_conversation_seq"),
        UniqueConstraint(
            "user_id", "client_request_id", name="uq_chat_messages_user_client_request"
        ),
        CheckConstraint("role IN ('user', 'assistant', 'system')", name="check_chat_message_role"),
        CheckConstraint(
            "status IN ('DONE', 'PARTIAL', 'REFUSED')", name="check_chat_message_status"
        ),
        CheckConstraint(
            "(client_request_id IS NULL) = (request_hash IS NULL)",
            name="check_chat_message_request_key",
        ),
    )

    conversation_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("chat_conversations.id", ondelete="CASCADE"), index=True, nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str | None] = mapped_column(String(16))
    run_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    client_request_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    request_hash: Mapped[str | None] = mapped_column(String(64))
    again_of_message_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("chat_messages.id")
    )
    claims: Mapped[Any | None] = mapped_column(JSONB)
    cards: Mapped[Any | None] = mapped_column(JSONB)
    assessments: Mapped[Any | None] = mapped_column(JSONB)
    plan: Mapped[Any | None] = mapped_column(JSONB)
    trace: Mapped[Any | None] = mapped_column(JSONB)
    basis: Mapped[Any | None] = mapped_column(JSONB)
    evidence: Mapped[Any | None] = mapped_column(JSONB)
    sources: Mapped[Any | None] = mapped_column(JSONB)
    usage: Mapped[Any | None] = mapped_column(JSONB)
    meta: Mapped[Any | None] = mapped_column(JSONB)
    source_pitch_ids: Mapped[list[str] | None] = mapped_column(JSONB)
    source_take_ids: Mapped[list[str] | None] = mapped_column(JSONB)


class ChatRun(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "chat_runs"
    __table_args__ = (
        Index(
            "uq_chat_runs_one_active",
            "conversation_id",
            unique=True,
            postgresql_where=text("status IN ('QUEUED', 'RUNNING', 'WAITING_APPROVAL')"),
        ),
        CheckConstraint(
            "first_trigger IN ('USER_MESSAGE', 'TAKE_REVIEWED')",
            name="check_chat_run_first_trigger",
        ),
        CheckConstraint(
            "status IN ('QUEUED', 'RUNNING', 'WAITING_APPROVAL', 'DONE', 'PARTIAL', "
            "'REFUSED', 'FAILED', 'CANCELLED', 'EXPIRED')",
            name="check_chat_run_status",
        ),
        CheckConstraint(
            "row_revision >= 0 AND state_revision >= 0", name="check_chat_run_revisions"
        ),
    )

    conversation_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("chat_conversations.id", ondelete="CASCADE"), index=True, nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    message_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("chat_messages.id", ondelete="CASCADE")
    )
    first_trigger: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="QUEUED")
    row_revision: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    state_revision: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    scope_pitch_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    input: Mapped[Any] = mapped_column(JSONB, nullable=False)
    state: Mapped[Any | None] = mapped_column(JSONB)
    turn_calls: Mapped[Any | None] = mapped_column(JSONB)
    progress_turn_call: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    progress_seq: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    plan: Mapped[Any | None] = mapped_column(JSONB)
    trace: Mapped[Any | None] = mapped_column(JSONB)
    interim_message: Mapped[Any | None] = mapped_column(JSONB)
    budget_used: Mapped[Any | None] = mapped_column(JSONB)
    usage: Mapped[Any | None] = mapped_column(JSONB)
    read_sources: Mapped[Any | None] = mapped_column(JSONB)
    error_code: Mapped[str | None] = mapped_column(String(40))
    retry_after_s: Mapped[int | None] = mapped_column(Integer)
    accepted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    deadline_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    approval_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approval_ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    lease_owner: Mapped[str | None] = mapped_column(String(100))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    fencing_token: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default="0"
    )


class ChatAction(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "chat_actions"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "type", "idempotency_key", name="uq_chat_actions_user_type_key"
        ),
        UniqueConstraint("run_id", "action_id", name="uq_chat_actions_run_action"),
        CheckConstraint(
            "type IN ('FETCH_PITCH', 'FETCH_TAKE', 'REASSESS', 'SAVE_GOAL', 'START_PRACTICE')",
            name="check_chat_action_type",
        ),
        CheckConstraint(
            "autonomy IN ('AUTO', 'AUTO_UNDO', 'ASK')", name="check_chat_action_autonomy"
        ),
        CheckConstraint(
            "status IN ('PENDING', 'RUNNING', 'SUCCEEDED', 'FAILED')",
            name="check_chat_action_status",
        ),
    )

    conversation_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("chat_conversations.id", ondelete="CASCADE"), index=True, nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    run_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    action_id: Mapped[str] = mapped_column(String(64), nullable=False)
    type: Mapped[str] = mapped_column(String(20), nullable=False)
    args: Mapped[Any] = mapped_column(JSONB, nullable=False)
    args_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    autonomy: Mapped[str] = mapped_column(String(10), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    args_hash: Mapped[str | None] = mapped_column(String(64))
    approved: Mapped[bool | None] = mapped_column(Boolean)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="PENDING")
    result: Mapped[Any | None] = mapped_column(JSONB)
    source_take_ids: Mapped[list[str] | None] = mapped_column(JSONB)
    undone_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ChatDailyUsage(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "chat_daily_usage"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "day",
            name="uq_chat_daily_usage_user_day",
            postgresql_nulls_not_distinct=True,
        ),
        CheckConstraint(
            "turns >= 0 AND reassessments >= 0 AND goal_checks >= 0 "
            "AND reserved >= 0 AND sent >= 0",
            name="check_chat_daily_usage_counts",
        ),
        CheckConstraint(
            "(user_id IS NULL AND turns = 0 AND reassessments = 0 AND goal_checks = 0) "
            "OR (user_id IS NOT NULL AND reserved = 0 AND sent = 0)",
            name="check_chat_daily_usage_scope",
        ),
    )

    user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE")
    )
    day: Mapped[date] = mapped_column(Date, index=True, nullable=False)
    turns: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    reassessments: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    goal_checks: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    reserved: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    sent: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")


class ChatLease(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "chat_leases"
    __table_args__ = (
        UniqueConstraint(
            "conversation_id",
            "goal_version",
            "take_id",
            "review_revision",
            name="uq_chat_leases_goal_check",
        ),
        Index(
            "uq_chat_leases_one_cleanup",
            "kind",
            unique=True,
            postgresql_where=text("kind = 'CLEANUP'"),
        ),
        CheckConstraint("kind IN ('GOAL_CHECK', 'CLEANUP')", name="check_chat_lease_kind"),
        CheckConstraint("status IN ('PENDING', 'DONE')", name="check_chat_lease_status"),
        CheckConstraint(
            "(kind = 'GOAL_CHECK') = (conversation_id IS NOT NULL AND goal_version IS NOT NULL "
            "AND take_id IS NOT NULL AND review_revision IS NOT NULL AND status IS NOT NULL)",
            name="check_chat_lease_goal_key",
        ),
    )

    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("chat_conversations.id", ondelete="CASCADE")
    )
    goal_version: Mapped[int | None] = mapped_column(Integer)
    take_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, index=True)
    review_revision: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str | None] = mapped_column(String(10))
    run_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    owner: Mapped[str | None] = mapped_column(String(100))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    fencing_token: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default="0"
    )


class ChatRunLog(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "chat_run_logs"

    run_id: Mapped[uuid.UUID] = mapped_column(Uuid, unique=True, nullable=False)
    day: Mapped[date] = mapped_column(Date, index=True, nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    ms: Mapped[int] = mapped_column(Integer, nullable=False)
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)


class ChatDiagnostic(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "chat_diagnostics"
    __table_args__ = (UniqueConstraint("run_id", "turn_call", name="uq_chat_diagnostics_run_call"),)

    conversation_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("chat_conversations.id", ondelete="CASCADE"), index=True, nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    run_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    turn_call: Mapped[int] = mapped_column(Integer, nullable=False)
    request_id: Mapped[str] = mapped_column(String(64), nullable=False)
    diagnostics: Mapped[Any] = mapped_column(JSONB, nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
