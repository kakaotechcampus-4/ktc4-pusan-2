"""엔드포인트: google/start · callback · 쿠키 · 리다이렉트."""

from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from pitch_coach_backend.core.config import settings
from pitch_coach_backend.module.auth import google, service, state_store
from pitch_coach_backend.module.auth.controller import (
    BROWSER_COOKIE_NAME,
    REFRESH_COOKIE_NAME,
    browser_cookie_name,
)
from pitch_coach_backend.module.auth.dependencies import CSRF_COOKIE_NAME
from pitch_coach_backend.module.auth.exception import (
    EmailAlreadyRegistered,
    GoogleIdentityRejected,
    GoogleTokenExchangeFailed,
)
from tests.auth.conftest import CALLBACK, NON_ASCII, START, start_login

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


def test_start_failure_redirects_with_login_failed(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """DB 오류 등으로 시작을 못 해도 500 JSON 대신 프론트로 보낸다."""

    def fail(*args: Any, **kwargs: Any) -> None:
        raise OperationalError("INSERT", {}, Exception("down"))

    monkeypatch.setattr(state_store, "save", fail)

    res = client.get(START, follow_redirects=False)

    assert res.status_code == 302
    assert res.headers["location"].startswith(settings.frontend_base_url)
    assert "auth_error=login_failed" in res.headers["location"]
    assert not any(name.startswith(BROWSER_COOKIE_NAME) for name in res.cookies)


def test_login_started_in_two_tabs_completes_in_both(
    client: TestClient, fake_google: dict[str, Any]
) -> None:
    """나중 탭의 임시 쿠키가 먼저 탭의 것을 덮어쓰지 않는다."""
    first = start_login(client)
    second = start_login(client)

    for state in (first, second):
        res = client.get(CALLBACK, params={"code": "c", "state": state}, follow_redirects=False)
        assert "auth_error" not in res.headers["location"]
        assert REFRESH_COOKIE_NAME in res.cookies
        assert client.cookies.get(browser_cookie_name(state)) is None


def test_hostile_state_in_the_callback_does_not_crash(client: TestClient) -> None:
    """state 는 쿠키 이름에 들어간다. 쿠키 이름에 못 쓰는 글자가 와도 500 이 아니다."""
    for params in ({"code": "c", "state": "a b;c"}, {"error": "access_denied", "state": "a b;c"}):
        res = client.get(CALLBACK, params=params, follow_redirects=False)

        assert res.status_code == 302
        assert "auth_error=" in res.headers["location"]


@pytest.mark.parametrize(
    "hostile",
    [
        "https://evil.com",
        "//evil.com",
        "/\\evil.com",
        # 앞의 '/' 를 떼면 절대 URL 이 된다. 실제로 뚫렸던 형태다.
        "/https://evil.com",
        "/http://evil.com",
        "//evil.com/path",
        None,
        "",
    ],
)
def test_return_to_never_leaves_the_frontend(hostile: str | None) -> None:
    """로그인 직후 사용자를 외부로 보내면 그대로 피싱 통로가 된다."""
    from pitch_coach_backend.module.auth.controller import _frontend_url

    destination = _frontend_url(hostile)

    assert destination.startswith(settings.frontend_base_url)
    assert "evil.com" not in destination


def test_return_to_keeps_internal_paths() -> None:
    from pitch_coach_backend.module.auth.controller import _frontend_url

    assert _frontend_url("/pitches/1") == f"{settings.frontend_base_url}/pitches/1"
    assert _frontend_url("/") == f"{settings.frontend_base_url}/"


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

    assert res.status_code == 302
    assert res.headers["location"].startswith(settings.frontend_base_url)
    assert "auth_error=invalid_request" in res.headers["location"]
    assert REFRESH_COOKIE_NAME not in res.cookies


def test_callback_without_the_browser_cookie_is_rejected(
    client: TestClient, fake_google: dict[str, Any]
) -> None:
    state = start_login(client)
    client.cookies.delete(browser_cookie_name(state))

    res = client.get(CALLBACK, params={"code": "c", "state": state}, follow_redirects=False)

    assert res.status_code == 302
    assert "auth_error=invalid_request" in res.headers["location"]
    assert REFRESH_COOKIE_NAME not in res.cookies


@pytest.mark.parametrize(
    ("raised", "expected_code"),
    [
        (GoogleTokenExchangeFailed, "token_exchange_failed"),
        (GoogleIdentityRejected, "identity_rejected"),
        (EmailAlreadyRegistered, "email_already_registered"),
    ],
)
def test_callback_failure_redirects_with_a_code_instead_of_json(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    raised: type[Exception],
    expected_code: str,
) -> None:
    """브라우저가 최상위로 이동해 온 요청이다. JSON 을 주면 사용자가 그대로 본다."""

    def fail(**kwargs: Any) -> None:
        raise raised()

    monkeypatch.setattr(service.google, "exchange_code_for_id_token", fail)
    started = client.get(START, follow_redirects=False)
    state = parse_qs(urlparse(started.headers["location"]).query)["state"][0]

    res = client.get(CALLBACK, params={"code": "c", "state": state}, follow_redirects=False)

    assert res.status_code == 302
    assert res.headers["location"].startswith(settings.frontend_base_url)
    assert f"auth_error={expected_code}" in res.headers["location"]
    assert REFRESH_COOKIE_NAME not in res.cookies
    # 실패해도 임시 쿠키는 정리한다
    assert client.cookies.get(browser_cookie_name(state)) is None


def test_unexpected_callback_error_redirects_with_login_failed(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """도메인 예외가 아닌 오류(DB 오류 등)도 500 JSON 대신 프론트로 보낸다."""

    def fail(**kwargs: Any) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(service.google, "exchange_code_for_id_token", fail)
    started = client.get(START, follow_redirects=False)
    state = parse_qs(urlparse(started.headers["location"]).query)["state"][0]

    res = client.get(CALLBACK, params={"code": "c", "state": state}, follow_redirects=False)

    assert res.status_code == 302
    assert res.headers["location"].startswith(settings.frontend_base_url)
    assert "auth_error=login_failed" in res.headers["location"]
    assert REFRESH_COOKIE_NAME not in res.cookies
    assert client.cookies.get(browser_cookie_name(state)) is None


def test_cancelled_consent_redirects_to_the_frontend(client: TestClient) -> None:
    """동의 화면에서 취소하면 구글이 code 없이 error 만 보낸다.

    필수 파라미터로 두면 사용자가 원인 모를 422 JSON 을 보게 된다.
    """
    res = client.get(
        CALLBACK, params={"error": "access_denied", "state": "st"}, follow_redirects=False
    )

    assert res.status_code == 302
    assert res.headers["location"].startswith(settings.frontend_base_url)
    assert "auth_error=access_denied" in res.headers["location"]


def test_callback_without_any_parameter_redirects_too(client: TestClient) -> None:
    res = client.get(CALLBACK, follow_redirects=False)

    assert res.status_code == 302
    assert "auth_error=invalid_request" in res.headers["location"]


def test_cancelled_consent_clears_the_temporary_cookie(client: TestClient) -> None:
    state = start_login(client)
    assert browser_cookie_name(state) in client.cookies

    client.get(CALLBACK, params={"error": "access_denied", "state": state}, follow_redirects=False)

    assert client.cookies.get(browser_cookie_name(state)) is None


def test_google_error_code_is_not_reflected_verbatim(client: TestClient) -> None:
    """error 값은 구글이 주지만 URL 에 그대로 실으면 주입 통로가 된다."""
    res = client.get(
        CALLBACK,
        params={"error": "<script>alert(1)</script>", "state": "st"},
        follow_redirects=False,
    )

    location = res.headers["location"]
    assert "script" not in location
    assert "auth_error=login_failed" in location


# --- 헤더 · 쿠키 값 ------------------------------------------------------


def test_non_ascii_browser_cookie_is_rejected_not_crashed(
    client: TestClient, fake_google: dict[str, Any]
) -> None:
    state = start_login(client)

    res = client.get(
        CALLBACK,
        params={"code": "c", "state": state},
        headers={"Cookie": b"%s=%s" % (browser_cookie_name(state).encode(), NON_ASCII)},
        follow_redirects=False,
    )

    assert res.status_code == 302
    assert "auth_error=invalid_request" in res.headers["location"]
