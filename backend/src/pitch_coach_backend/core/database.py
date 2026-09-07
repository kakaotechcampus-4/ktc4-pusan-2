from collections.abc import Generator
from datetime import datetime

from sqlalchemy import DateTime, create_engine, func
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    Session,
    mapped_column,
    sessionmaker,
)

from pitch_coach_backend.core.config import settings

# pool_pre_ping: 끊어진 커넥션을 쓰기 전에 감지 (RDS 유휴 종료 대비)
engine = create_engine(settings.database_url, pool_pre_ping=True)

# expire_on_commit=False: commit 후에도 entity 속성을 다시 조회 없이 읽을 수 있게 함
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    """created_at / updated_at 공통 컬럼. entity 에 Base 와 함께 상속한다."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


def get_db() -> Generator[Session]:
    """요청 단위 세션. 커밋은 service 가 하고, 여기서는 열고 닫기만 한다."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
