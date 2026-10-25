"""엔드포인트: logout · /users/me."""

from typing import Any

import pytest
from fastapi.testclient import TestClient

from pitch_coach_backend.module.auth.controller import REFRESH_COOKIE_NAME
from tests.auth.conftest import FRONTEND_ORIGIN, ME, REFRESH, csrf_headers, do_login

pytestmark = pytest.mark.usefixtures("_clean_redis", "_use_test_redis")


LOGOUT = "/api/auth/logout"


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

    assert client.post(LOGOUT, headers=FRONTEND_ORIGIN).status_code == 401


# --- /users/me ----------------------------------------------------------


def test_me_returns_the_logged_in_user(client: TestClient, fake_google: dict[str, Any]) -> None:
    do_login(client)
    access = client.post(REFRESH, headers=csrf_headers(client)).json()["access_token"]

    res = client.get(ME, headers={"Authorization": f"Bearer {access}"})

    assert res.status_code == 200
    body = res.json()
    assert body["email"] == "new@example.com"
    assert body["name"] == "새사용자"
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
