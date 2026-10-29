"""Take별 설문과 마이페이지 의견. AI 리뷰와 별개다."""

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


class UserSuggestion(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "user_suggestions"
    __table_args__ = (
        UniqueConstraint("user_id", "request_id", name="uq_user_suggestions_request"),
        CheckConstraint(
            "char_length(content) BETWEEN 1 AND 2000", name="ck_user_suggestions_content"
        ),
        CheckConstraint(
            "source = 'MY_PAGE' AND request_id IS NOT NULL", name="ck_user_suggestions_source"
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
    # 1차 배포 때는 홈 팝업(RATING_POPUP)에서도 받았다. 지금은 MY_PAGE 만 있다
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    category: Mapped[str] = mapped_column(
        String(16), nullable=False, default=SuggestionCategory.OTHER, server_default="OTHER"
    )
    request_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)


class TakeSurvey(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """리포트 하나에 남기는 설문. Take 당 한 번이고 고치지 않는다.

    탈퇴하거나 발표 · Take 를 지워도 응답은 남기고 연결만 끊는다.
    """

    __tablename__ = "take_surveys"
    __table_args__ = (
        UniqueConstraint("take_id", name="uq_take_surveys_take"),
        CheckConstraint("accuracy_rating BETWEEN 1 AND 5", name="ck_take_surveys_accuracy"),
        CheckConstraint("usefulness_rating BETWEEN 1 AND 5", name="ck_take_surveys_usefulness"),
        CheckConstraint(
            "comment IS NULL OR char_length(comment) BETWEEN 1 AND 2000",
            name="ck_take_surveys_comment",
        ),
        CheckConstraint("question_version >= 1", name="ck_take_surveys_question_version"),
        Index("ix_take_surveys_user", "user_id"),
        Index("ix_take_surveys_created", "created_at", "id"),
    )

    user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey("users.id", ondelete="SET NULL", name="fk_take_surveys_user"),
        nullable=True,
    )
    take_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey("takes.id", ondelete="SET NULL", name="fk_take_surveys_take"),
        nullable=True,
    )
    # 분석 결과가 내 발표와 맞았다고 느낀 정도 (1~5)
    accuracy_rating: Mapped[int] = mapped_column(Integer, nullable=False)
    # 피드백이 구체적이고 도움이 됐다고 느낀 정도 (1~5)
    usefulness_rating: Mapped[int] = mapped_column(Integer, nullable=False)
    # 부정확하거나 아쉬웠던 점. 선택이다
    comment: Mapped[str | None] = mapped_column(Text)
    # FE 가 보여 준 문항 문구의 버전. 문구를 고친 앞뒤 응답을 나눠 보려고 남긴다
    question_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
