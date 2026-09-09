"""인증 관련 테이블.

Google 이 발급한 access·refresh token 은 어느 테이블에도 저장하지 않는다.
신원 확인에만 쓰고 콜백 처리가 끝나면 버린다.
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from pitch_coach_backend.core.database import Base, TimestampMixin, UUIDPrimaryKeyMixin


class OAuthAccount(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """소셜 계정과 User 의 연결. 지금은 Google 전용이라 User 와 1:1 이다."""

    __tablename__ = "oauth_accounts"
    __table_args__ = (
        # 같은 제공자의 같은 sub 가 두 계정에 붙는 것을 DB 가 막는다.
        # 동시 최초 로그인 경쟁도 이 제약으로 걸러진다.
        UniqueConstraint("provider", "provider_subject", name="uq_oauth_accounts_provider_subject"),
    )

    # unique=True 로 1:1 을 강제한다. 다른 provider 를 붙일 때 이 제약을 푼다.
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    # Google ID Token 의 sub. 이메일이 바뀌어도 변하지 않는 유일한 식별자다.
    provider_subject: Mapped[str] = mapped_column(String(255), nullable=False)
    # 제공자가 준 이메일. 참고용이며 사용자 식별에 쓰지 않는다.
    provider_email: Mapped[str | None] = mapped_column(String(255))


class RefreshToken(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Refresh 토큰의 해시. 원문은 쿠키에만 있고 DB 에 남기지 않는다."""

    __tablename__ = "refresh_tokens"

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    # SHA-256 hex. UNIQUE 라서 회전 시 같은 해시가 두 번 저장되지 않는다.
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    # 로그인 1회(= 브라우저 1개)를 묶는 값. 회전해도 유지되어 재사용 탐지 시
    # 이 device_id 의 토큰을 통째로 폐기할 수 있다.
    device_id: Mapped[uuid.UUID] = mapped_column(Uuid, index=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # NULL 이면 살아 있는 토큰. 회전·로그아웃·재사용 탐지 시 시각이 찍힌다.
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
