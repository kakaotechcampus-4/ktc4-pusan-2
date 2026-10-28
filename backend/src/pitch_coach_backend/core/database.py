import uuid
from collections.abc import Callable, Generator
from datetime import datetime

from sqlalchemy import DateTime, Uuid, create_engine, func
from sqlalchemy import exc as sa_exc
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


class UUIDPrimaryKeyMixin:
    """UUIDv7 PK. 시간순 정렬되어 인덱스 단편화가 적고 외부 노출에도 안전하다."""

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=uuid.uuid7, sort_order=-10
    )


class CreatedAtMixin:
    """생성 시각만 필요한 테이블용. 한 번 쓰이면 갱신되지 않는 기록성 행에 쓴다."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        sort_order=10,
    )


class TimestampMixin:
    """created_at / updated_at 공통 컬럼. sort_order 로 테이블 맨 뒤에 놓인다."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        sort_order=10,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
        sort_order=10,
    )


def get_db() -> Generator[Session]:
    """요청 단위 세션. 커밋은 service 가 하고, 여기서는 열고 닫기만 한다."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


class SessionPerCall:
    """요청 밖(WebSocket · 백그라운드 태스크)에서 DB 를 쓰는 저장소의 바탕.

    요청 스코프 세션(`get_db`)은 엔드포인트가 돌아오는 순간 닫혀서 쓸 수 없다. 그래서 부를 때마다
    짧은 세션을 새로 열고 닫는다. 동기 SQLAlchemy 라 threadpool 안에서 부른다.
    """

    def __init__(self, session_factory: Callable[[], Session] = SessionLocal) -> None:
        self._session_factory = session_factory

    def _with[T](self, fn: Callable[..., T], *args: object) -> T:
        """짧은 세션을 열어 fn(db, *args) 를 부르고 닫는다 (threadpool 안에서 돈다)."""
        db = self._session_factory()
        try:
            return fn(db, *args)
        finally:
            db.close()


def is_transient_db_error(error: BaseException) -> bool:
    """다시 하면 될 수 있는 DB 실패인가 — 연결 끊김 · 잠금 대기 · 교착 · 시간 초과.

    값이 칸에 맞지 않거나(`DataError`) 제약을 어겼거나(`IntegrityError`) 코드가 틀렸으면 아니다.
    몇 번을 다시 해도 같다.
    """
    if isinstance(error, sa_exc.DBAPIError) and error.connection_invalidated:
        return True
    return isinstance(
        error,
        sa_exc.OperationalError
        | sa_exc.InterfaceError
        | sa_exc.TimeoutError
        | TimeoutError
        | ConnectionError,
    )
