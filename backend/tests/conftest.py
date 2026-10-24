"""공통 fixture.

- 테스트 DB 는 DATABASE_URL 의 DB 이름에 `_test` 를 붙인 것을 쓴다. 없으면 만든다.
  개발 DB 를 건드리지 않기 위한 장치다.
- 각 테스트는 트랜잭션 안에서 돌고 끝나면 롤백된다. service 가 commit 해도 savepoint 로 처리된다.
- pitch_coach_backend 는 fixture 안에서 import 한다.
  settings 가 아래 환경변수를 먼저 읽어야 하기 때문.
"""

import os
import uuid
from collections.abc import Awaitable, Callable, Generator
from io import BytesIO
from pathlib import Path

import pytest
from dotenv import dotenv_values
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

_dotenv = dotenv_values(Path(__file__).resolve().parents[1] / ".env")
for _target, _source in (
    ("DATABASE_URL", "LOCAL_DATABASE_URL"),
    ("REDIS_URL", "LOCAL_REDIS_URL"),
):
    _value = os.environ.get(_source) or _dotenv.get(_source)
    if _value:
        os.environ[_target] = _value

# 테스트 토큰은 항상 같은 키로 서명된다 (.env 값보다 우선)
os.environ.setdefault("JWT_SECRET_KEY", "test-only-secret-do-not-use-in-production")

# 필수 설정이 늘면 .env 가 없는 CI 에서 settings import 자체가 실패한다.
# 구글 값은 테스트에서 실제로 쓰이지 않고(외부 HTTP 는 모킹), 존재하기만 하면 된다.
os.environ.setdefault("GOOGLE_CLIENT_ID", "test-client-id.apps.googleusercontent.com")
os.environ.setdefault("GOOGLE_CLIENT_SECRET", "test-client-secret")
# Deepgram 도 마찬가지. 실제 연결은 가짜 서버로 대체한다.
os.environ.setdefault("DEEPGRAM_API_KEY", "test-deepgram-key")
# S3 도 마찬가지. 업로드는 테스트에서 모킹한다.
os.environ.setdefault("S3_BUCKET_NAME", _dotenv.get("S3_BUCKET_NAME", "test-bucket"))


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
def user_id(db_session: Session) -> uuid.UUID:
    from pitch_coach_backend.module.user.entity import User

    user = User(email=f"{uuid.uuid4()}@example.com", name="테스트 사용자")
    db_session.add(user)
    db_session.flush()
    return user.id


@pytest.fixture
def make_pdf() -> Callable[[int], bytes]:
    """쪽수가 pages 인 빈 PDF. 발표자료 업로드는 파일을 직접 열어 쪽수를 센다."""
    from pypdf import PdfWriter

    def make(pages: int = 1) -> bytes:
        writer = PdfWriter()
        for _ in range(pages):
            writer.add_blank_page(width=200, height=200)
        buffer = BytesIO()
        writer.write(buffer)
        return buffer.getvalue()

    return make


class NoCoachClient:
    """AI 코치가 없는 것처럼 군다 (모든 호출이 실패). 코치를 확인하는 테스트는 다른 것을 끼운다."""

    async def evaluate(self, request: dict) -> None:
        return None

    async def finalize(self, request: dict) -> None:
        return None

    async def plan(self, request: dict) -> None:
        return None


class NoCoachStore:
    """코치 저장소. 아무것도 저장하지 않는다 — 실제 DbCoachStore 는 SessionLocal 로 새 커넥션을
    열어 테스트 DB 가 아닌 개발 DB 를 본다."""

    def __init__(self) -> None:
        self.failed: list[uuid.UUID] = []

    async def append_events(self, take_id, events) -> None:
        return None

    async def load_events(self, take_id) -> list:
        return []

    async def load_finalize_source(self, take_id) -> None:
        return None

    async def save_take_result(self, take_id, reply, events) -> bool:
        return False

    async def fail_take_result(self, take_id) -> bool:
        self.failed.append(take_id)
        return True

    async def finalizing_takes(self, stale_before=None, *, after=None, limit=100) -> list:
        return []

    async def build_plan_request(self, pitch_id) -> None:
        return None

    async def save_plan(self, pitch_id, request, reply, requested_at) -> bool:
        return False

    async def purge_expired_raw(self, now) -> int:
        return 0


class _NoopParseRunner:
    async def run(self, ticket: object) -> None:
        return None


async def _no_abandon_timers(store: object) -> None:
    return None


def _no_ready_sweep() -> Callable[[], Awaitable[None]]:
    async def stop() -> None:
        return None

    return stop


async def _no_coach_background(client: object, store: object):
    async def stop() -> None:
        return None

    return stop


@pytest.fixture
def client(db_session: Session, monkeypatch: pytest.MonkeyPatch) -> Generator[TestClient]:
    from pitch_coach_backend import main
    from pitch_coach_backend.coach import dependencies as coach_dependencies
    from pitch_coach_backend.core.database import get_db
    from pitch_coach_backend.main import app
    from pitch_coach_backend.module.pitch import service as pitch_service
    from pitch_coach_backend.module.pitch.dependencies import get_script_parse_runner

    monkeypatch.setattr(main, "get_s3", lambda: None)
    # 발표 삭제·회원 탈퇴 뒤 S3 파일 정리가 실제 버킷을 부르지 않게 막는다
    monkeypatch.setattr(pitch_service, "delete_prefix", lambda prefix: None)
    # 앱이 뜰 때 진행 중인 Take 를 SessionLocal(테스트 DB 가 아니다)로 읽고 타이머를 건다. 막는다
    monkeypatch.setattr(main, "arm_abandoned_takes", _no_abandon_timers)
    # 시작하지 않은 Take 정리도 SessionLocal 로 돈다. 테스트는 sweep_once 에 세션을 넘겨 따로 본다
    monkeypatch.setattr(main, "start_ready_sweep", _no_ready_sweep)
    # 같은 이유로 만들다 만 Take 결과 이어 가기 · 원자료 보관 주기 작업도 막는다
    monkeypatch.setattr(main, "start_coach_background", _no_coach_background)
    app.dependency_overrides[get_db] = lambda: db_session
    # 업로드 뒤 백그라운드 대본 파싱이 실제 AI 서버를 부르지 않게 막는다.
    # 파싱을 확인하는 테스트는 가짜 파서를 끼운 runner 로 다시 바꿔 끼운다
    app.dependency_overrides[get_script_parse_runner] = lambda: _NoopParseRunner()
    # 코치 AI · 코치 저장소도 실제 것을 부르지 않게 막는다. 프로세스에 하나인 객체를 바꿔 끼운다 —
    # HTTP 요청이 발표 중 처리 · 코치에 알리는 쪽(module/hooks.py 의 구현)도 같은 것을 꺼내 쓴다.
    # 코치를 확인하는 테스트는 다시 바꿔 끼운다
    monkeypatch.setattr(coach_dependencies, "_coach_client", NoCoachClient())
    monkeypatch.setattr(coach_dependencies, "_coach_store", NoCoachStore())
    # base_url 이 https 여야 한다. 쿠키를 Secure 로 심는데(cookie_secure 기본값 True)
    # http 로는 httpx 가 그 쿠키를 되돌려 보내지 않아 인증 흐름이 통째로 막힌다.
    # 운영과 같은 설정을 그대로 테스트하려고 설정을 낮추는 대신 https 를 쓴다.
    with TestClient(app, base_url="https://testserver") as c:
        yield c
    app.dependency_overrides.clear()
