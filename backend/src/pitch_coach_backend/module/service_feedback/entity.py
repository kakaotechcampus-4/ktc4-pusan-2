"""서비스 별점·의견·팝업 표시 승인 기록. AI 리뷰와 별개다."""

import uuid

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from pitch_coach_backend.core.database import Base, CreatedAtMixin, UUIDPrimaryKeyMixin


class ServiceFeedback(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "service_feedbacks"
    __table_args__ = (
        UniqueConstraint("user_id", name="uq_service_feedbacks_user"),
        CheckConstraint("rating BETWEEN 1 AND 5", name="ck_service_feedbacks_rating"),
        Index("ix_service_feedbacks_created", "created_at", "id"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    rating: Mapped[int] = mapped_column(Integer, nullable=False)


class UserSuggestion(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "user_suggestions"
    __table_args__ = (
        UniqueConstraint("service_feedback_id", name="uq_user_suggestions_feedback"),
        UniqueConstraint("user_id", "request_id", name="uq_user_suggestions_request"),
        CheckConstraint(
            "char_length(content) BETWEEN 1 AND 2000", name="ck_user_suggestions_content"
        ),
        CheckConstraint(
            "(source = 'RATING_POPUP' AND service_feedback_id IS NOT NULL AND request_id IS NULL) "
            "OR (source = 'MY_PAGE' AND service_feedback_id IS NULL AND request_id IS NOT NULL)",
            name="ck_user_suggestions_source",
        ),
        Index("ix_user_suggestions_created", "created_at", "id"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("users.id", ondelete="CASCADE", name="fk_user_suggestions_user"),
        nullable=False,
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    service_feedback_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("service_feedbacks.id", ondelete="CASCADE")
    )
    request_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)


class ServiceFeedbackImpression(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "service_feedback_impressions"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "display_request_id", name="uq_service_feedback_impressions_request"
        ),
        UniqueConstraint(
            "user_id", "display_number", name="uq_service_feedback_impressions_number"
        ),
        CheckConstraint(
            "display_number BETWEEN 1 AND 3", name="ck_service_feedback_impressions_number"
        ),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    display_request_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    display_number: Mapped[int] = mapped_column(Integer, nullable=False)
