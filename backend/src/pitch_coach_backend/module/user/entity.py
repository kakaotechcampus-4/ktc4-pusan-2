from sqlalchemy import Boolean, String
from sqlalchemy.orm import Mapped, mapped_column

from pitch_coach_backend.core.database import Base, TimestampMixin, UUIDPrimaryKeyMixin

NAME_MAX_LENGTH = 100


class User(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(NAME_MAX_LENGTH), nullable=False)
    # 1초 원자료(시선 · 음량 기록)를 Take 가 끝난 뒤에도 남겨 다시 쓰는 것에 대한 동의.
    # 마이페이지에서 받는다. Take 는 시작할 때 이 값을 `takes.raw_data_consent` 로 복사해
    # 그 값만 본다 — 철회하면 철회한 뒤에 시작하는 Take 부터다
    # (`docs/ai/architecture.md` 1-1 '원자료 사용 동의')
    raw_data_consent: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
