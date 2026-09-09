"""공통 fixture.

- 테스트 DB 는 DATABASE_URL 의 DB 이름에 `_test` 를 붙인 것을 쓴다. 없으면 만든다.
  개발 DB 를 건드리지 않기 위한 장치다.
- 각 테스트는 트랜잭션 안에서 돌고 끝나면 롤백된다. service 가 commit 해도 savepoint 로 처리된다.
- pitch_coach_backend 는 fixture 안에서 import 한다.
  settings 가 아래 환경변수를 먼저 읽어야 하기 때문.
"""

import os
from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

# 테스트 토큰은 항상 같은 키로 서명된다 (.env 값보다 우선)
os.environ.setdefault("JWT_SECRET_KEY", "test-only-secret-do-not-use-in-production")

# 필수 설정이 늘면 .env 가 없는 CI 에서 settings import 자체가 실패한다.
# 구글 값은 테스트에서 실제로 쓰이지 않고(외부 HTTP 는 모킹), 존재하기만 하면 된다.
os.environ.setdefault("GOOGLE_CLIENT_ID", "test-client-id.apps.googleusercontent.com")
os.environ.setdefault("GOOGLE_CLIENT_SECRET", "test-client-secret")


def _test_database_url() -> str:
    from pitch_coach_backend.core.config import settings

    url = make_url(settings.database_url)
    assert url.database, "DATABASE_URL 에 DB 이름이 없습니다."
    return url.set(database=f"{url.database}_test").render_as_string(hide_password=False)


def _ensure_database(url_str: str) -> None:
    url = make_url(url_str)
    admin = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        exists = conn.execute(
            text("SELECT 1 FROM pg_database WHERE datname = :name"), {"name": url.database}
        ).scalar()
        if not exists:
            conn.execute(text(f'CREATE DATABASE "{url.database}"'))
    admin.dispose()


@pytest.fixture(scope="session")
def engine() -> Generator[Engine]:
    from pitch_coach_backend import entities  # noqa: F401
    from pitch_coach_backend.core.database import Base

    url = _test_database_url()
    _ensure_database(url)
    engine = create_engine(url)
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield engine
    Base.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture
def db_session(engine: Engine) -> Generator[Session]:
    with engine.connect() as conn:
        outer = conn.begin()
        session = Session(
            bind=conn,
            join_transaction_mode="create_savepoint",
            autoflush=False,
            expire_on_commit=False,
        )
        try:
            yield session
        finally:
            session.close()
            outer.rollback()


@pytest.fixture
def client(db_session: Session) -> Generator[TestClient]:
    from pitch_coach_backend.core.database import get_db
    from pitch_coach_backend.main import app

    app.dependency_overrides[get_db] = lambda: db_session
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
