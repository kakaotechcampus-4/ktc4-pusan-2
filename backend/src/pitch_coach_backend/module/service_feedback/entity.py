"""1차 배포 서비스 별점과 사용자 의견. AI 리뷰와 별개다."""

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
from pitch_coach_backend.module.service_feedback.enums import SuggestionCategory


class FirstReleaseFeedback(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """1차 배포의 홈 팝업에서 받은 사용자당 서비스 별점. 새로 받지 않고 보관만 한다."""

    __tablename__ = "first_release_feedbacks"
    __table_args__ = (
        UniqueConstraint("user_id", name="uq_first_release_feedbacks_user"),
        CheckConstraint("rating BETWEEN 1 AND 5", name="ck_first_release_feedbacks_rating"),
        Index("ix_first_release_feedbacks_created", "created_at", "id"),
    )

    user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey("users.id", ondelete="SET NULL", name="fk_first_release_feedbacks_user"),
        nullable=True,
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
        CheckConstraint(
            "category IN ('POSITIVE', 'IMPROVEMENT', 'PROBLEM', 'QUESTION', 'OTHER')",
            name="ck_user_suggestions_category",
        ),
        Index("ix_user_suggestions_created", "created_at", "id"),
    )

    user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey("users.id", ondelete="SET NULL", name="fk_user_suggestions_user"),
        nullable=True,
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    # RATING_POPUP 은 1차 배포 팝업에서 별점과 같이 받은 의견이다. 지금은 MY_PAGE 만 새로 받는다
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    category: Mapped[str] = mapped_column(
        String(16), nullable=False, default=SuggestionCategory.OTHER, server_default="OTHER"
    )
    # 1차 배포 별점(`first_release_feedbacks`)의 ID. 칸 이름은 1차 배포 때 그대로다
    service_feedback_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("first_release_feedbacks.id", ondelete="CASCADE")
    )
    request_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
