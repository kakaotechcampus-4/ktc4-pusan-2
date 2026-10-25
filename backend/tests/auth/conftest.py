"""auth 테스트 공통: 테스트용 Redis · 가짜 Google · 로그인 · CSRF 도우미.

구글 통신(code 교환·ID Token 검증)만 가짜로 바꾸고 나머지는 실제로 돈다.
DB 는 진짜 트랜잭션, Redis 도 진짜(15번 DB)다. 검증 로직을 통째로 패치하지 않는다.
"""

from collections.abc import Generator
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
import redis
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from pitch_coach_backend.core import security
from pitch_coach_backend.core.config import settings
from pitch_coach_backend.module.auth import google, service
from pitch_coach_backend.module.auth.dependencies import CSRF_COOKIE_NAME, CSRF_HEADER_NAME

TEST_REDIS_DB = 15


@pytest.fixture
def redis_client() -> Generator[redis.Redis]:
    from pitch_coach_backend.core.config import settings

    url = urlparse(settings.redis_url)._replace(path=f"/{TEST_REDIS_DB}").geturl()
    client = redis.Redis.from_url(url, decode_responses=True)
    client.flushdb()
    yield client
    client.flushdb()
    client.close()


@pytest.fixture
def fake_google(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """구글 왕복만 가짜로. identity 를 바꿔가며 시나리오를 만든다."""
    box: dict[str, Any] = {
        "identity": google.GoogleIdentity(
            subject="sub-1", email="new@example.com", name="새사용자"
        ),
        "exchanged": [],
    }

    def fake_exchange(*, code: str, code_verifier: str) -> str:
        box["exchanged"].append({"code": code, "code_verifier": code_verifier})
        return "signed-by-google"

    def fake_verify(id_token: str, *, expected_nonce: str) -> google.GoogleIdentity:
        box["nonce_seen"] = expected_nonce
        return box["identity"]

    monkeypatch.setattr(service.google, "exchange_code_for_id_token", fake_exchange)
    monkeypatch.setattr(service.google, "verify_id_token", fake_verify)
    return box


def login(db: Session, redis_client: redis.Redis, *, return_to: str = "/") -> service.IssuedSession:
    """start -> callback 을 한 번 돌린다. 실제 흐름 그대로 state 를 주고받는다."""
    started = service.start_login(redis_client, return_to=return_to)
    state = _state_from(started.authorization_url)
    return service.complete_login(
        db,
        redis_client,
        code="auth-code",
        state=state,
        browser_token=started.browser_token,
    )


def _past_grace(db: Session, refresh_token: str) -> None:
    """이 토큰을 회전한 지 유예 시간이 지난 것으로 만든다."""
    db.execute(
        text(
            "UPDATE refresh_tokens SET revoked_at = revoked_at - make_interval(secs => :secs)"
            " WHERE token_hash = :token_hash"
        ),
        {
            "secs": settings.refresh_token_reuse_grace_seconds + 1,
            "token_hash": security.hash_refresh_token(refresh_token),
        },
    )


def _state_from(url: str) -> str:
    from urllib.parse import parse_qs

    return parse_qs(urlparse(url).query)["state"][0]


START = "/api/auth/google/start"
CALLBACK = "/api/auth/google/callback"
REFRESH = "/api/auth/refresh"
ME = "/api/users/me"
FRONTEND_ORIGIN = {"Origin": settings.frontend_base_url}


@pytest.fixture
def _clean_redis() -> Generator[None]:
    url = urlparse(settings.redis_url)._replace(path=f"/{TEST_REDIS_DB}").geturl()
    client = redis.Redis.from_url(url, decode_responses=True)
    client.flushdb()
    yield
    client.flushdb()
    client.close()


@pytest.fixture
def _use_test_redis(monkeypatch: pytest.MonkeyPatch) -> None:
    """앱이 15번 DB 를 쓰게 한다. 개발용 0번을 건드리지 않는다."""
    from pitch_coach_backend.core import redis as core_redis
    from pitch_coach_backend.main import app

    url = urlparse(settings.redis_url)._replace(path=f"/{TEST_REDIS_DB}").geturl()
    client = redis.Redis.from_url(url, decode_responses=True)
    app.dependency_overrides[core_redis.get_redis] = lambda: client
    yield
    app.dependency_overrides.pop(core_redis.get_redis, None)
    client.close()


def start_login(client: TestClient) -> str:
    """start 를 부르고 구글로 보낸 state 를 돌려준다."""
    started = client.get(START, follow_redirects=False)
    return parse_qs(urlparse(started.headers["location"]).query)["state"][0]


def do_login(client: TestClient) -> None:
    """start -> callback 을 브라우저처럼 통과시킨다. 이후 client 는 쿠키를 갖는다."""
    state = start_login(client)
    done = client.get(CALLBACK, params={"code": "c", "state": state}, follow_redirects=False)
    assert done.status_code == 302, done.text


def csrf_token_header(client: TestClient) -> dict[str, str]:
    """double-submit 헤더만. 출처 헤더를 직접 정하는 테스트가 쓴다."""
    return {CSRF_HEADER_NAME: client.cookies[CSRF_COOKIE_NAME]}


def csrf_headers(client: TestClient) -> dict[str, str]:
    """브라우저가 보내는 모양. 브라우저는 POST 에 Origin 을 늘 붙인다."""
    return csrf_token_header(client) | FRONTEND_ORIGIN


# Starlette 은 헤더·쿠키를 latin-1 로 디코딩한다. 0xFF 바이트는 'ÿ'(비ASCII str)가 된다.
NON_ASCII = "ÿ".encode("latin-1")
