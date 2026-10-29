"""엔드포인트: refresh · CSRF · Origin/Referer."""

from typing import Any

import pytest
from fastapi.testclient import TestClient

from pitch_coach_backend.core.config import settings
from pitch_coach_backend.module.auth.controller import REFRESH_COOKIE_NAME
from pitch_coach_backend.module.auth.dependencies import CSRF_HEADER_NAME
from tests.auth.conftest import (
    FRONTEND_ORIGIN,
    NON_ASCII,
    REFRESH,
    csrf_headers,
    csrf_token_header,
    do_login,
)

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
    res = client.post(REFRESH, headers=FRONTEND_ORIGIN)

    assert res.status_code == 401
    assert res.headers["www-authenticate"] == "Bearer"


def test_refresh_without_the_csrf_header_is_rejected(
    client: TestClient, fake_google: dict[str, Any]
) -> None:
    """쿠키만으로 통과하면 다른 사이트가 사용자를 시켜 갱신을 호출할 수 있다."""
    do_login(client)

    res = client.post(REFRESH, headers=FRONTEND_ORIGIN)

    assert res.status_code == 401


def test_refresh_with_a_wrong_csrf_header_is_rejected(
    client: TestClient, fake_google: dict[str, Any]
) -> None:
    do_login(client)

    res = client.post(REFRESH, headers=FRONTEND_ORIGIN | {CSRF_HEADER_NAME: "attacker-guess"})

    assert res.status_code == 401


def test_refresh_from_a_foreign_origin_is_rejected(
    client: TestClient, fake_google: dict[str, Any]
) -> None:
    do_login(client)
    headers = csrf_token_header(client) | {"Origin": "https://evil.example.com"}

    res = client.post(REFRESH, headers=headers)

    assert res.status_code == 401


def test_refresh_from_the_frontend_origin_is_allowed(
    client: TestClient, fake_google: dict[str, Any]
) -> None:
    do_login(client)
    headers = csrf_token_header(client) | FRONTEND_ORIGIN

    assert client.post(REFRESH, headers=headers).status_code == 200


def test_refresh_with_only_a_referer_from_the_frontend_is_allowed(
    client: TestClient, fake_google: dict[str, Any]
) -> None:
    """Origin 이 빠진 요청도 있다. 그때는 Referer 의 출처를 본다."""
    do_login(client)
    headers = csrf_token_header(client) | {"Referer": f"{settings.frontend_base_url}/login?next=/"}

    assert client.post(REFRESH, headers=headers).status_code == 200


def test_refresh_with_a_foreign_referer_is_rejected(
    client: TestClient, fake_google: dict[str, Any]
) -> None:
    do_login(client)
    headers = csrf_token_header(client) | {"Referer": "https://evil.example.com/attack"}

    assert client.post(REFRESH, headers=headers).status_code == 401


def test_refresh_without_any_origin_or_referer_is_rejected(
    client: TestClient, fake_google: dict[str, Any]
) -> None:
    """출처를 확인할 방법이 없는 요청은 막는다. OWASP 가 권하는 쪽이다."""
    do_login(client)

    assert client.post(REFRESH, headers=csrf_token_header(client)).status_code == 401


def test_a_trusted_referer_does_not_rescue_a_foreign_origin(
    client: TestClient, fake_google: dict[str, Any]
) -> None:
    """Origin 이 있으면 그것만 본다. Referer 를 덧붙여 우회할 수 없다."""
    do_login(client)
    headers = csrf_token_header(client) | {
        "Origin": "https://evil.example.com",
        "Referer": f"{settings.frontend_base_url}/",
    }

    assert client.post(REFRESH, headers=headers).status_code == 401


def test_a_referer_that_merely_starts_with_the_frontend_url_is_rejected(
    client: TestClient, fake_google: dict[str, Any]
) -> None:
    """localhost:3000.evil.example.com 은 남의 사이트다. 출처는 통째로 같아야 한다."""
    do_login(client)
    headers = csrf_token_header(client) | {
        "Referer": f"{settings.frontend_base_url}.evil.example.com/"
    }

    assert client.post(REFRESH, headers=headers).status_code == 401


def test_a_malformed_referer_is_rejected_not_crashed(
    client: TestClient, fake_google: dict[str, Any]
) -> None:
    """헤더 값은 공격자가 정한다. urlsplit 은 깨진 URL 에 ValueError 를 낸다."""
    do_login(client)
    headers = csrf_token_header(client) | {"Referer": "http://["}

    assert client.post(REFRESH, headers=headers).status_code == 401


def test_replaying_an_old_refresh_cookie_is_rejected(
    client: TestClient, fake_google: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "refresh_token_reuse_grace_seconds", 0)
    do_login(client)
    stolen = client.cookies[REFRESH_COOKIE_NAME]
    client.post(REFRESH, headers=csrf_headers(client))

    client.cookies.set(REFRESH_COOKIE_NAME, stolen, path="/api/auth")
    res = client.post(REFRESH, headers=csrf_headers(client))

    assert res.status_code == 401


def test_refresh_retried_with_the_cookie_it_just_rotated_keeps_the_session(
    client: TestClient, fake_google: dict[str, Any]
) -> None:
    """회전 응답을 받기 전에 새로고침하면 브라우저는 옛 쿠키를 다시 보낸다."""
    do_login(client)
    before = client.cookies[REFRESH_COOKIE_NAME]
    client.post(REFRESH, headers=csrf_headers(client))
    client.cookies.set(REFRESH_COOKIE_NAME, before, path="/api/auth")  # 응답을 버렸다

    assert client.post(REFRESH, headers=csrf_headers(client)).status_code == 200
    assert client.post(REFRESH, headers=csrf_headers(client)).status_code == 200


# --- 헤더 · 쿠키 값 ------------------------------------------------------


def test_non_ascii_csrf_header_is_rejected_not_crashed(
    client: TestClient, fake_google: dict[str, Any]
) -> None:
    """헤더 값은 공격자가 정한다. hmac.compare_digest 는 비ASCII str 에 TypeError 를 낸다.

    그대로 넘기면 401 이 아니라 500 이 나가면서 스택트레이스가 로그를 더럽힌다.
    """
    do_login(client)

    res = client.post(REFRESH, headers=FRONTEND_ORIGIN | {CSRF_HEADER_NAME: NON_ASCII})

    assert res.status_code == 401
