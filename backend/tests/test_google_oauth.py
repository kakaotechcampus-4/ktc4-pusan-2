"""Google 연동 모듈 테스트.

핵심 원칙: **외부 HTTP 통신만 모킹하고 검증 로직은 실제로 실행한다.**
verify_id_token 을 통째로 패치하면 "잘못된 토큰을 실제로 거부하는지" 를 확인할 수 없다.
그래서 테스트용 RSA 키로 진짜 RS256 토큰을 서명하고, JWKS 조회만 그 키로 바꿔치기한다.
"""

import time
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from pitch_coach_backend.core.config import settings
from pitch_coach_backend.module.auth import google
from pitch_coach_backend.module.auth.exception import (
    GoogleIdentityRejected,
    GoogleTokenExchangeFailed,
)

NONCE = "test-nonce"

_private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture(autouse=True)
def _use_test_signing_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """JWKS HTTP 조회만 테스트 키로 바꾼다. 서명 검증 자체는 그대로 돈다."""

    class _Key:
        key = _private_key.public_key()

    monkeypatch.setattr(
        google._jwk_client, "get_signing_key_from_jwt", lambda _token: _Key(), raising=True
    )


def make_id_token(**overrides: Any) -> str:
    now = int(time.time())
    claims: dict[str, Any] = {
        "iss": "https://accounts.google.com",
        "aud": settings.google_client_id,
        "sub": "google-sub-123",
        "email": "user@example.com",
        "email_verified": True,
        "name": "김테스트",
        "nonce": NONCE,
        "iat": now,
        "exp": now + 3600,
    }
    claims.update(overrides)
    claims = {k: v for k, v in claims.items() if v is not ...}
    return jwt.encode(claims, _private_key, algorithm="RS256")


# --- PKCE ---------------------------------------------------------------


def test_pkce_challenge_is_derived_from_verifier() -> None:
    import base64
    import hashlib

    verifier, challenge = google.build_pkce_pair()
    expected = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    )

    assert challenge == expected
    # base64url 은 패딩 '=' 을 쓰지 않는다. 붙으면 구글이 거부한다.
    assert "=" not in challenge


def test_pkce_pairs_are_unique() -> None:
    verifiers = {google.build_pkce_pair()[0] for _ in range(50)}
    assert len(verifiers) == 50


# --- 인증 URL -----------------------------------------------------------


def test_authorization_url_carries_everything_google_needs() -> None:
    from urllib.parse import parse_qs, urlparse

    url = google.build_authorization_url(state="st", nonce="nc", code_challenge="ch")
    parsed = urlparse(url)
    params = {k: v[0] for k, v in parse_qs(parsed.query).items()}

    assert url.startswith(google.AUTHORIZATION_ENDPOINT)
    assert params["client_id"] == settings.google_client_id
    assert params["redirect_uri"] == settings.google_redirect_uri
    assert params["response_type"] == "code"
    assert params["state"] == "st"
    assert params["nonce"] == "nc"
    assert params["code_challenge"] == "ch"
    assert params["code_challenge_method"] == "S256"


def test_authorization_url_requests_only_identity_scopes() -> None:
    from urllib.parse import parse_qs, urlparse

    url = google.build_authorization_url(state="st", nonce="nc", code_challenge="ch")
    scope = parse_qs(urlparse(url).query)["scope"][0]

    assert set(scope.split()) == {"openid", "email", "profile"}


def test_authorization_url_never_asks_for_a_google_refresh_token() -> None:
    """access_type=offline 을 보내지 않는다. 구글 refresh token 은 받지도 저장하지도 않는다."""
    url = google.build_authorization_url(state="st", nonce="nc", code_challenge="ch")

    assert "access_type=offline" not in url


# --- ID Token 검증 -------------------------------------------------------


def test_valid_id_token_yields_identity() -> None:
    identity = google.verify_id_token(make_id_token(), expected_nonce=NONCE)

    assert identity.subject == "google-sub-123"
    assert identity.email == "user@example.com"
    assert identity.name == "김테스트"


def test_token_signed_by_another_key_is_rejected() -> None:
    attacker_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = int(time.time())
    forged = jwt.encode(
        {
            "iss": "https://accounts.google.com",
            "aud": settings.google_client_id,
            "sub": "attacker",
            "email": "victim@example.com",
            "email_verified": True,
            "nonce": NONCE,
            "iat": now,
            "exp": now + 3600,
        },
        attacker_key,
        algorithm="RS256",
    )

    with pytest.raises(GoogleIdentityRejected):
        google.verify_id_token(forged, expected_nonce=NONCE)


def test_unsigned_token_is_rejected() -> None:
    """alg=none 공격. PyJWT 가 algorithms=['RS256'] 로 막는다."""
    now = int(time.time())
    unsigned = jwt.encode(
        {
            "iss": "https://accounts.google.com",
            "aud": settings.google_client_id,
            "sub": "x",
            "exp": now + 3600,
        },
        key="",
        algorithm="none",
    )

    with pytest.raises(GoogleIdentityRejected):
        google.verify_id_token(unsigned, expected_nonce=NONCE)


def test_expired_token_is_rejected() -> None:
    now = int(time.time())
    with pytest.raises(GoogleIdentityRejected):
        google.verify_id_token(make_id_token(iat=now - 7200, exp=now - 3600), expected_nonce=NONCE)


def test_token_for_another_client_is_rejected() -> None:
    """다른 앱에서 받은 구글 토큰을 우리 서버에 들고 오는 공격을 aud 가 막는다."""
    with pytest.raises(GoogleIdentityRejected):
        google.verify_id_token(
            make_id_token(aud="another-app.apps.googleusercontent.com"), expected_nonce=NONCE
        )


def test_token_from_wrong_issuer_is_rejected() -> None:
    with pytest.raises(GoogleIdentityRejected):
        google.verify_id_token(make_id_token(iss="https://evil.example.com"), expected_nonce=NONCE)


@pytest.mark.parametrize("issuer", google.VALID_ISSUERS)
def test_both_google_issuer_spellings_are_accepted(issuer: str) -> None:
    """구글은 iss 로 두 형태를 모두 쓴다. 둘 다 통과해야 한다."""
    identity = google.verify_id_token(make_id_token(iss=issuer), expected_nonce=NONCE)

    assert identity.subject == "google-sub-123"


def test_nonce_mismatch_is_rejected() -> None:
    with pytest.raises(GoogleIdentityRejected, match="일치하지 않습니다"):
        google.verify_id_token(make_id_token(nonce="different"), expected_nonce=NONCE)


def test_missing_nonce_is_rejected() -> None:
    with pytest.raises(GoogleIdentityRejected):
        google.verify_id_token(make_id_token(nonce=...), expected_nonce=NONCE)


def test_unverified_email_is_rejected() -> None:
    """미검증 이메일을 받으면 남의 주소가 계정 이메일이 된다."""
    with pytest.raises(GoogleIdentityRejected, match="이메일이 확인되지 않은"):
        google.verify_id_token(make_id_token(email_verified=False), expected_nonce=NONCE)


def test_missing_email_verified_claim_is_rejected() -> None:
    with pytest.raises(GoogleIdentityRejected):
        google.verify_id_token(make_id_token(email_verified=...), expected_nonce=NONCE)


def test_missing_email_is_rejected() -> None:
    with pytest.raises(GoogleIdentityRejected, match="이메일을 받지 못했습니다"):
        google.verify_id_token(make_id_token(email=...), expected_nonce=NONCE)


def test_missing_name_falls_back_to_email_local_part() -> None:
    identity = google.verify_id_token(make_id_token(name=...), expected_nonce=NONCE)

    assert identity.name == "user"


def test_garbage_token_is_rejected() -> None:
    with pytest.raises(GoogleIdentityRejected):
        google.verify_id_token("not-a-jwt", expected_nonce=NONCE)


# --- code 교환 ----------------------------------------------------------


def test_exchange_sends_secret_and_verifier(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: dict[str, Any] = {}

    class _Response:
        status_code = 200

        def json(self) -> dict[str, str]:
            return {"id_token": "tok"}

    def fake_post(url: str, **kwargs: Any) -> _Response:
        sent["url"] = url
        sent["data"] = kwargs["data"]
        return _Response()

    monkeypatch.setattr(google.httpx, "post", fake_post)
    result = google.exchange_code_for_id_token(code="the-code", code_verifier="the-verifier")

    assert result == "tok"
    assert sent["url"] == google.TOKEN_ENDPOINT
    assert sent["data"]["code"] == "the-code"
    assert sent["data"]["code_verifier"] == "the-verifier"
    assert sent["data"]["grant_type"] == "authorization_code"
    assert sent["data"]["client_secret"] == settings.google_client_secret
    assert sent["data"]["redirect_uri"] == settings.google_redirect_uri


def test_exchange_failure_does_not_leak_google_response(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Response:
        status_code = 400

        def json(self) -> dict[str, str]:
            return {"error": "invalid_grant", "error_description": "secret-ish detail"}

    monkeypatch.setattr(google.httpx, "post", lambda url, **kw: _Response())

    with pytest.raises(GoogleTokenExchangeFailed) as exc:
        google.exchange_code_for_id_token(code="bad", code_verifier="v")

    assert "invalid_grant" not in str(exc.value)
    assert "secret-ish" not in str(exc.value)


def test_exchange_network_error_becomes_domain_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(url: str, **kwargs: Any) -> None:
        raise google.httpx.ConnectError("연결 실패")

    monkeypatch.setattr(google.httpx, "post", boom)

    with pytest.raises(GoogleTokenExchangeFailed, match="연결하지 못했습니다"):
        google.exchange_code_for_id_token(code="c", code_verifier="v")


def test_exchange_without_id_token_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """200 이어도 id_token 이 없을 수 있다. 그대로 꺼내면 KeyError -> 500 이 된다."""

    class _Response:
        status_code = 200

        def json(self) -> dict[str, str]:
            return {"access_token": "구글이-준-액세스-토큰", "token_type": "Bearer"}

    monkeypatch.setattr(google.httpx, "post", lambda url, **kw: _Response())

    with pytest.raises(GoogleTokenExchangeFailed):
        google.exchange_code_for_id_token(code="c", code_verifier="v")


def test_exchange_with_non_json_body_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Response:
        status_code = 200

        def json(self) -> dict[str, str]:
            raise ValueError("JSON 이 아니다")

    monkeypatch.setattr(google.httpx, "post", lambda url, **kw: _Response())

    with pytest.raises(GoogleTokenExchangeFailed):
        google.exchange_code_for_id_token(code="c", code_verifier="v")
