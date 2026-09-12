"""Google OAuth 2.0 어댑터.

DB 도 Redis 도 모른다. 인증 URL 을 만들고, code 를 토큰으로 바꾸고,
ID Token 을 검증해서 신원(sub·email·name)을 돌려주는 일만 한다.
테스트에서는 이 파일의 외부 HTTP 호출만 갈아끼우고 검증 로직은 그대로 실행한다.

Authlib 을 쓰지 않고 httpx + PyJWT 로 직접 구현한다. 흐름이 코드에 그대로 보이고,
검증을 어디까지 하는지 리뷰에서 확인할 수 있다.
"""

import base64
import hashlib
import secrets
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import httpx
import jwt
from jwt import PyJWKClient

from pitch_coach_backend.core.config import settings
from pitch_coach_backend.module.auth.exception import (
    GoogleIdentityRejected,
    GoogleTokenExchangeFailed,
)

AUTHORIZATION_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
JWKS_URI = "https://www.googleapis.com/oauth2/v3/certs"

# ID Token 의 iss 는 이 둘 중 하나다. Google 이 두 값을 모두 쓴다.
VALID_ISSUERS = ("https://accounts.google.com", "accounts.google.com")

# 신원 확인에 필요한 최소 scope. 이 이상 요구하지 않는다.
SCOPE = "openid email profile"

# 서명 검증 시 허용하는 시계 오차
LEEWAY_SECONDS = 30

EXCHANGE_TIMEOUT_SECONDS = 10.0

# JWKS 는 자주 바뀌지 않는다. 클라이언트가 키를 캐시해 매 로그인마다 받지 않는다.
_jwk_client = PyJWKClient(JWKS_URI, cache_keys=True)


@dataclass(frozen=True, slots=True)
class GoogleIdentity:
    """검증이 끝난 ID Token 에서 뽑은 신원. 이 값만 service 로 넘어간다."""

    subject: str
    email: str
    name: str


def build_pkce_pair() -> tuple[str, str]:
    """(verifier, challenge). verifier 는 서버가 보관하고 challenge 만 Google 에 보낸다.

    중간에 code 를 가로채도 verifier 없이는 토큰으로 바꿀 수 없다 (RFC 7636).
    """
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return verifier, challenge


def build_authorization_url(*, state: str, nonce: str, code_challenge: str) -> str:
    params = {
        "client_id": settings.google_client_id,
        "redirect_uri": settings.google_redirect_uri,
        "response_type": "code",
        "scope": SCOPE,
        "state": state,
        "nonce": nonce,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        # 계정 선택 화면을 항상 띄운다. 계정이 여러 개인 사용자가 원하는 쪽을 고를 수 있다.
        "prompt": "select_account",
    }
    return f"{AUTHORIZATION_ENDPOINT}?{urlencode(params)}"


def exchange_code_for_id_token(*, code: str, code_verifier: str) -> str:
    """code 를 ID Token 으로 교환한다. client_secret 이 나가는 유일한 지점이다.

    응답의 access_token 은 쓰지 않고 버리고, refresh_token 은 아예 요청하지 않는다.
    필요한 것이 id_token 하나뿐이라 그것만 돌려준다 — 남은 값을 실수로 저장할 여지를 없앤다.
    """
    try:
        response = httpx.post(
            TOKEN_ENDPOINT,
            data={
                "code": code,
                "client_id": settings.google_client_id,
                "client_secret": settings.google_client_secret,
                "redirect_uri": settings.google_redirect_uri,
                "grant_type": "authorization_code",
                "code_verifier": code_verifier,
            },
            timeout=EXCHANGE_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as e:
        raise GoogleTokenExchangeFailed("구글 인증 서버에 연결하지 못했습니다.") from e

    if response.status_code != httpx.codes.OK:
        # 구글 응답 본문은 로그에도 남기지 않는다. code 와 client_secret 이 섞여 있다.
        raise GoogleTokenExchangeFailed()

    try:
        payload: dict[str, Any] = response.json()
    except ValueError as e:
        raise GoogleTokenExchangeFailed() from e

    # 200 인데 id_token 이 없을 수 있다 (scope 에 openid 가 빠진 경우 등).
    # 그대로 꺼내면 KeyError -> 500 이 된다. 도메인 예외로 바꾼다.
    id_token = payload.get("id_token")
    if not isinstance(id_token, str) or not id_token:
        raise GoogleTokenExchangeFailed()
    return id_token


def verify_id_token(id_token: str, *, expected_nonce: str) -> GoogleIdentity:
    """ID Token 을 검증하고 신원을 뽑는다.

    서명(Google JWKS)·iss·aud·exp 는 PyJWT 가, nonce·email_verified 는 아래에서 본다.
    하나라도 어긋나면 로그인을 거부한다. 이 함수가 인증의 실제 관문이다.
    """
    try:
        signing_key = _jwk_client.get_signing_key_from_jwt(id_token)
        claims = jwt.decode(
            id_token,
            signing_key.key,
            algorithms=["RS256"],
            audience=settings.google_client_id,
            issuer=list(VALID_ISSUERS),
            leeway=LEEWAY_SECONDS,
            options={"require": ["iss", "aud", "exp", "sub"]},
        )
    except jwt.PyJWTError as e:
        raise GoogleIdentityRejected("구글 ID 토큰 검증에 실패했습니다.") from e

    # nonce 로 "이 응답이 우리가 시작한 그 요청의 것인지" 를 확인한다.
    # state 가 브라우저 세션을, nonce 가 ID Token 자체를 묶는다. 둘 다 필요하다.
    if claims.get("nonce") != expected_nonce:
        raise GoogleIdentityRejected("인증 요청과 응답이 일치하지 않습니다.")

    # 미검증 이메일을 받으면 남의 주소를 그대로 계정 이메일로 쓰게 된다.
    if claims.get("email_verified") is not True:
        raise GoogleIdentityRejected("이메일이 확인되지 않은 구글 계정입니다.")

    email = claims.get("email")
    if not email:
        raise GoogleIdentityRejected("구글 계정에서 이메일을 받지 못했습니다.")

    # name 은 scope 에 profile 이 있어도 비어 있을 수 있다. 이메일 앞부분으로 대체한다.
    return GoogleIdentity(
        subject=claims["sub"],
        email=email,
        name=claims.get("name") or email.split("@")[0],
    )
