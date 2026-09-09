"""엔드포인트 계층 테스트.

TestClient 는 쿠키를 실제로 저장하고 다시 보낸다. 그래서 "콜백이 심은 쿠키로
refresh 가 되는지" 를 브라우저와 같은 방식으로 확인할 수 있다.
구글 통신만 가짜고 나머지(쿠키·CSRF·리다이렉트·JWT)는 전부 실제 코드다.
"""

from collections.abc import Generator
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
import redis
from fastapi.testclient import TestClient

from pitch_coach_backend.core.config import settings
from pitch_coach_backend.module.auth import google
from pitch_coach_backend.module.auth.controller import (
    BROWSER_COOKIE_NAME,
    REFRESH_COOKIE_NAME,
)
from pitch_coach_backend.module.auth.dependencies import CSRF_COOKIE_NAME, CSRF_HEADER_NAME

TEST_REDIS_DB = 15
START = "/api/auth/google/start"
CALLBACK = "/api/auth/google/callback"
REFRESH = "/api/auth/refresh"
LOGOUT = "/api/auth/logout"
ME = "/api/users/me"


@pytest.fixture(autouse=True)
def _clean_redis() -> Generator[None]:
    url = urlparse(settings.redis_url)._replace(path=f"/{TEST_REDIS_DB}").geturl()
    client = redis.Redis.from_url(url, decode_responses=True)
    client.flushdb()
    yield
    client.flushdb()
    client.close()


@pytest.fixture(autouse=True)
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


@pytest.fixture
def fake_google(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    from pitch_coach_backend.module.auth import service

    box: dict[str, Any] = {
        "identity": google.GoogleIdentity(
            subject="sub-e2e", email="e2e@example.com", name="엔드투엔드"
        )
    }
    monkeypatch.setattr(
        service.google, "exchange_code_for_tokens", lambda **kw: {"id_token": "tok"}
    )
    monkeypatch.setattr(
        service.google, "verify_id_token", lambda token, *, expected_nonce: box["identity"]
    )
    return box


def do_login(client: TestClient) -> None:
    """start -> callback 을 브라우저처럼 통과시킨다. 이후 client 는 쿠키를 갖는다."""
    started = client.get(START, follow_redirects=False)
    state = parse_qs(urlparse(started.headers["location"]).query)["state"][0]
    done = client.get(CALLBACK, params={"code": "c", "state": state}, follow_redirects=False)
    assert done.status_code == 302, done.text


def csrf_headers(client: TestClient) -> dict[str, str]:
    return {CSRF_HEADER_NAME: client.cookies[CSRF_COOKIE_NAME]}


# --- start --------------------------------------------------------------


def test_start_redirects_to_google(client: TestClient) -> None:
    res = client.get(START, follow_redirects=False)

    assert res.status_code == 302
    assert res.headers["location"].startswith(google.AUTHORIZATION_ENDPOINT)


def test_start_sets_the_temporary_browser_cookie(client: TestClient) -> None:
    res = client.get(START, follow_redirects=False)

    cookie = res.headers["set-cookie"]
    assert BROWSER_COOKIE_NAME in cookie
    assert "HttpOnly" in cookie


def test_start_rejects_an_external_return_to(client: TestClient) -> None:
    """열린 리다이렉트 방지. 외부 주소는 조용히 '/' 로 바뀐다."""
    from pitch_coach_backend.module.auth.controller import _safe_return_to

    assert _safe_return_to("https://evil.com") == "/"
    assert _safe_return_to("//evil.com") == "/"
    assert _safe_return_to("/\\evil.com") == "/"
    assert _safe_return_to(None) == "/"
    assert _safe_return_to("/pitches/1") == "/pitches/1"


# --- callback -----------------------------------------------------------


def test_callback_sets_cookies_and_redirects_to_the_frontend(
    client: TestClient, fake_google: dict[str, Any]
) -> None:
    started = client.get(START, follow_redirects=False)
    state = parse_qs(urlparse(started.headers["location"]).query)["state"][0]

    res = client.get(CALLBACK, params={"code": "c", "state": state}, follow_redirects=False)

    assert res.status_code == 302
    assert res.headers["location"].startswith(settings.frontend_base_url)
    assert REFRESH_COOKIE_NAME in client.cookies
    assert CSRF_COOKIE_NAME in client.cookies


def test_callback_never_puts_tokens_in_the_url(
    client: TestClient, fake_google: dict[str, Any]
) -> None:
    """URL 에 토큰을 실으면 브라우저 기록·리퍼러·서버 로그에 남는다."""
    started = client.get(START, follow_redirects=False)
    state = parse_qs(urlparse(started.headers["location"]).query)["state"][0]

    res = client.get(CALLBACK, params={"code": "c", "state": state}, follow_redirects=False)

    location = res.headers["location"]
    assert "token" not in location.lower()
    assert urlparse(location).query == ""


def test_refresh_cookie_is_httponly_and_scoped(
    client: TestClient, fake_google: dict[str, Any]
) -> None:
    started = client.get(START, follow_redirects=False)
    state = parse_qs(urlparse(started.headers["location"]).query)["state"][0]
    res = client.get(CALLBACK, params={"code": "c", "state": state}, follow_redirects=False)

    cookies = res.headers.get_list("set-cookie")
    refresh = next(c for c in cookies if c.startswith(f"{REFRESH_COOKIE_NAME}="))
    assert "HttpOnly" in refresh
    assert "SameSite=lax" in refresh.replace("samesite", "SameSite")
    assert "Path=/api/auth" in refresh


def test_csrf_cookie_is_readable_by_the_frontend(
    client: TestClient, fake_google: dict[str, Any]
) -> None:
    """double-submit 이 동작하려면 JS 가 읽을 수 있어야 한다. HttpOnly 면 안 된다."""
    started = client.get(START, follow_redirects=False)
    state = parse_qs(urlparse(started.headers["location"]).query)["state"][0]
    res = client.get(CALLBACK, params={"code": "c", "state": state}, follow_redirects=False)

    csrf = next(
        c for c in res.headers.get_list("set-cookie") if c.startswith(f"{CSRF_COOKIE_NAME}=")
    )
    assert "HttpOnly" not in csrf
    assert "Path=/" in csrf


def test_callback_with_unknown_state_is_rejected(
    client: TestClient, fake_google: dict[str, Any]
) -> None:
    res = client.get(
        CALLBACK, params={"code": "c", "state": "never-issued"}, follow_redirects=False
    )

    assert res.status_code == 400
    assert res.json()["code"] == "BAD_REQUEST"


def test_callback_without_the_browser_cookie_is_rejected(
    client: TestClient, fake_google: dict[str, Any]
) -> None:
    started = client.get(START, follow_redirects=False)
    state = parse_qs(urlparse(started.headers["location"]).query)["state"][0]
    client.cookies.delete(BROWSER_COOKIE_NAME)

    res = client.get(CALLBACK, params={"code": "c", "state": state}, follow_redirects=False)

    assert res.status_code == 400


def test_callback_requires_code_and_state(client: TestClient) -> None:
    assert client.get(CALLBACK, follow_redirects=False).status_code == 422


# --- refresh ------------------------------------------------------------


def test_refresh_returns_an_access_token(client: TestClient, fake_google: dict[str, Any]) -> None:
    do_login(client)

    res = client.post(REFRESH, headers=csrf_headers(client))

    assert res.status_code == 200
    body = res.json()
    assert body["token_type"] == "Bearer"
    assert body["expires_in"] == settings.access_token_expire_minutes * 60
    assert body["access_token"]


def test_refresh_never_returns_the_refresh_token(
    client: TestClient, fake_google: dict[str, Any]
) -> None:
    do_login(client)
    before = client.cookies[REFRESH_COOKIE_NAME]

    body = client.post(REFRESH, headers=csrf_headers(client)).json()

    assert "refresh_token" not in body
    assert before not in str(body)


def test_refresh_rotates_the_cookie(client: TestClient, fake_google: dict[str, Any]) -> None:
    do_login(client)
    before = client.cookies[REFRESH_COOKIE_NAME]

    client.post(REFRESH, headers=csrf_headers(client))

    assert client.cookies[REFRESH_COOKIE_NAME] != before


def test_refresh_without_a_cookie_is_unauthorized(client: TestClient) -> None:
    res = client.post(REFRESH)

    assert res.status_code == 401
    assert res.headers["www-authenticate"] == "Bearer"


def test_refresh_without_the_csrf_header_is_rejected(
    client: TestClient, fake_google: dict[str, Any]
) -> None:
    """쿠키만으로 통과하면 다른 사이트가 사용자를 시켜 갱신을 호출할 수 있다."""
    do_login(client)

    res = client.post(REFRESH)

    assert res.status_code == 401


def test_refresh_with_a_wrong_csrf_header_is_rejected(
    client: TestClient, fake_google: dict[str, Any]
) -> None:
    do_login(client)

    res = client.post(REFRESH, headers={CSRF_HEADER_NAME: "attacker-guess"})

    assert res.status_code == 401


def test_refresh_from_a_foreign_origin_is_rejected(
    client: TestClient, fake_google: dict[str, Any]
) -> None:
    do_login(client)
    headers = csrf_headers(client) | {"Origin": "https://evil.example.com"}

    res = client.post(REFRESH, headers=headers)

    assert res.status_code == 401


def test_refresh_from_the_frontend_origin_is_allowed(
    client: TestClient, fake_google: dict[str, Any]
) -> None:
    do_login(client)
    headers = csrf_headers(client) | {"Origin": settings.frontend_base_url}

    assert client.post(REFRESH, headers=headers).status_code == 200


def test_replaying_an_old_refresh_cookie_is_rejected(
    client: TestClient, fake_google: dict[str, Any]
) -> None:
    do_login(client)
    stolen = client.cookies[REFRESH_COOKIE_NAME]
    client.post(REFRESH, headers=csrf_headers(client))

    client.cookies.set(REFRESH_COOKIE_NAME, stolen, path="/api/auth")
    res = client.post(REFRESH, headers=csrf_headers(client))

    assert res.status_code == 401


# --- logout -------------------------------------------------------------


def test_logout_clears_cookies(client: TestClient, fake_google: dict[str, Any]) -> None:
    do_login(client)

    res = client.post(LOGOUT, headers=csrf_headers(client))

    assert res.status_code == 200
    assert client.cookies.get(REFRESH_COOKIE_NAME) is None


def test_refresh_after_logout_is_rejected(client: TestClient, fake_google: dict[str, Any]) -> None:
    do_login(client)
    headers = csrf_headers(client)
    stolen = client.cookies[REFRESH_COOKIE_NAME]
    client.post(LOGOUT, headers=headers)

    client.cookies.set(REFRESH_COOKIE_NAME, stolen, path="/api/auth")
    assert client.post(REFRESH, headers=headers).status_code == 401


def test_logout_also_requires_csrf(client: TestClient, fake_google: dict[str, Any]) -> None:
    do_login(client)

    assert client.post(LOGOUT).status_code == 401


# --- /users/me ----------------------------------------------------------


def test_me_returns_the_logged_in_user(client: TestClient, fake_google: dict[str, Any]) -> None:
    do_login(client)
    access = client.post(REFRESH, headers=csrf_headers(client)).json()["access_token"]

    res = client.get(ME, headers={"Authorization": f"Bearer {access}"})

    assert res.status_code == 200
    body = res.json()
    assert body["email"] == "e2e@example.com"
    assert body["name"] == "엔드투엔드"
    assert "id" in body


def test_me_without_a_token_is_unauthorized(client: TestClient) -> None:
    res = client.get(ME)

    assert res.status_code == 401
    assert res.json()["code"] == "UNAUTHORIZED"


def test_me_with_a_garbage_token_is_unauthorized(client: TestClient) -> None:
    assert client.get(ME, headers={"Authorization": "Bearer not-a-jwt"}).status_code == 401


def test_me_does_not_accept_the_refresh_cookie(
    client: TestClient, fake_google: dict[str, Any]
) -> None:
    """도메인 API 는 Access JWT 로만 인증한다. 쿠키만으로 통과하면 안 된다."""
    do_login(client)

    assert client.get(ME).status_code == 401


def test_me_rejects_an_expired_access_token(
    client: TestClient, fake_google: dict[str, Any]
) -> None:
    from pitch_coach_backend.core.security import create_access_token

    do_login(client)
    access = client.post(REFRESH, headers=csrf_headers(client)).json()["access_token"]
    from pitch_coach_backend.core.security import decode_access_token

    user_id = decode_access_token(access)["sub"]
    expired = create_access_token(user_id, expires_minutes=-1)

    assert client.get(ME, headers={"Authorization": f"Bearer {expired}"}).status_code == 401
