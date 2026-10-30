from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from pitch_coach_backend.core.database import Base, TimestampMixin, UUIDPrimaryKeyMixin

NAME_MAX_LENGTH = 100


class User(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint(
            "(test_consent_at IS NULL) = (test_consent_version IS NULL)",
            name="ck_users_test_consent",
        ),
    )

    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(NAME_MAX_LENGTH), nullable=False)
    # 1초 원자료(시선 · 음량 기록)를 Take 가 끝난 뒤에도 남겨 다시 쓰는 것에 대한 동의.
    # 마이페이지에서 받는다. Take 는 시작할 때 이 값을 `takes.raw_data_consent` 로 복사해
    # 그 값만 본다 — 철회하면 철회한 뒤에 시작하는 Take 부터다
    # (`docs/ai/architecture.md` 1-1 '원자료 사용 동의')
    raw_data_consent: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    # 사용자 테스트 참여 동의. 수집하는 데이터 · 목적 · 보관 기간 안내를 보고 동의한 시각과
    # 그 안내문의 버전이다. 안내문이 바뀌면 FE 가 새 버전으로 다시 받는다
    test_consent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    test_consent_version: Mapped[int | None] = mapped_column(Integer)
