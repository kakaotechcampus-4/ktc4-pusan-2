"""진행 중인 인증 요청을 Redis 에 잠깐 보관한다.

Google 로 보냈다가 콜백으로 돌아올 때까지 서버가 기억해야 하는 값이 셋이다.

- state:    CSRF 방어. 콜백이 우리가 시작한 요청인지 확인한다
- nonce:    ID Token 재사용 방어. 토큰 안의 nonce 와 대조한다
- verifier: PKCE. code 를 토큰으로 바꿀 때 필요하다

이 값들은 세션이 아니라 "요청 1건" 에 묶인다. 그래서 DB 가 아니라 짧은 TTL 의
Redis 에 두고, 콜백에서 한 번 소비하면 지운다. 소비를 원자적으로 처리해
같은 state 로 두 번 콜백이 들어와도 한쪽만 통과한다.
"""

import json
import secrets
from dataclasses import dataclass

import redis

# Google 동의 화면에서 사용자가 머무를 수 있는 시간. 넉넉하되 길지 않게.
STATE_TTL_SECONDS = 600

_KEY_PREFIX = "oauth:state:"


@dataclass(frozen=True, slots=True)
class AuthorizationRequest:
    """콜백에서 복원한 인증 요청. 전부 서버만 알던 값이다."""

    nonce: str
    code_verifier: str
    # /auth/google/start 를 호출한 브라우저에만 심어둔 임시 쿠키 값.
    # state 를 훔쳐도 이 값이 없으면 콜백을 완성할 수 없다.
    browser_token: str
    return_to: str


def new_state() -> str:
    return secrets.token_urlsafe(32)


def new_nonce() -> str:
    return secrets.token_urlsafe(32)


def new_browser_token() -> str:
    return secrets.token_urlsafe(32)


def _key(state: str) -> str:
    return f"{_KEY_PREFIX}{state}"


def save(client: redis.Redis, state: str, request: AuthorizationRequest) -> None:
    client.set(
        _key(state),
        json.dumps(
            {
                "nonce": request.nonce,
                "code_verifier": request.code_verifier,
                "browser_token": request.browser_token,
                "return_to": request.return_to,
            }
        ),
        ex=STATE_TTL_SECONDS,
    )


def consume(client: redis.Redis, state: str) -> AuthorizationRequest | None:
    """읽으면서 지운다. 없거나 이미 소비됐으면 None.

    GETDEL 은 단일 명령이라 원자적이다. 같은 state 로 콜백이 두 번 들어와도
    두 번째는 None 을 받는다 (재생 공격 차단).
    """
    raw = client.getdel(_key(state))
    if raw is None:
        return None
    data = json.loads(raw)
    return AuthorizationRequest(
        nonce=data["nonce"],
        code_verifier=data["code_verifier"],
        browser_token=data["browser_token"],
        return_to=data["return_to"],
    )
