"""진행 중인 인증 요청을 PostgreSQL 에 잠깐 보관한다.

Google 로 보냈다가 콜백으로 돌아올 때까지 서버가 기억해야 하는 값이 셋이다.

- state:    CSRF 방어. 콜백이 우리가 시작한 요청인지 확인한다
- nonce:    ID Token 재사용 방어. 토큰 안의 nonce 와 대조한다
- verifier: PKCE. code 를 토큰으로 바꿀 때 필요하다

이 값들은 세션이 아니라 "요청 1건" 에 묶인다. 10분 만료를 달아 oauth_authorization_requests
에 두고, 콜백에서 한 번 소비하면 지운다. 소비를 DELETE ... RETURNING 한 문장으로 처리해
같은 state 로 두 번 콜백이 들어와도 한쪽만 통과한다. 커밋은 service 가 한다.
"""

import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from pitch_coach_backend.module.auth.entity import OAuthAuthorizationRequest

# Google 동의 화면에서 사용자가 머무를 수 있는 시간. 넉넉하되 길지 않게.
STATE_TTL_SECONDS = 600


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


def save(db: Session, state: str, request: AuthorizationRequest) -> None:
    """새 요청을 넣는다. 콜백 없이 만료된 요청도 이때 지운다.

    정리 작업을 따로 돌리지 않는다. 로그인을 시작할 때마다 지우므로 남는 행은
    마지막으로 로그인을 시작한 때 아직 10분이 지나지 않았던 것뿐이다.
    다른 요청이 지우고 있는 행은 건너뛴다. 정리 때문에 로그인 시작이 기다리지 않는다.
    """
    now = datetime.now(UTC)
    expired = (
        select(OAuthAuthorizationRequest.state)
        .where(OAuthAuthorizationRequest.expires_at <= now)
        .with_for_update(skip_locked=True)
    )
    db.execute(
        delete(OAuthAuthorizationRequest).where(OAuthAuthorizationRequest.state.in_(expired))
    )
    db.add(
        OAuthAuthorizationRequest(
            state=state,
            nonce=request.nonce,
            code_verifier=request.code_verifier,
            browser_token=request.browser_token,
            return_to=request.return_to,
            expires_at=now + timedelta(seconds=STATE_TTL_SECONDS),
        )
    )
    db.flush()


def consume(db: Session, state: str) -> AuthorizationRequest | None:
    """읽으면서 지운다. 없거나 이미 소비됐거나 만료됐으면 None.

    DELETE ... RETURNING 은 한 문장이라 같은 행을 두 요청이 함께 지울 수 없다. 늦은 쪽은
    앞선 쪽이 커밋할 때까지 기다렸다가 지울 행이 없어 None 을 받는다 (재생 공격 차단).
    만료된 행도 여기서 지운다.
    """
    row = db.execute(
        delete(OAuthAuthorizationRequest)
        .where(OAuthAuthorizationRequest.state == state)
        .returning(
            OAuthAuthorizationRequest.nonce,
            OAuthAuthorizationRequest.code_verifier,
            OAuthAuthorizationRequest.browser_token,
            OAuthAuthorizationRequest.return_to,
            OAuthAuthorizationRequest.expires_at,
        )
    ).one_or_none()
    if row is None or row.expires_at <= datetime.now(UTC):
        return None
    return AuthorizationRequest(
        nonce=row.nonce,
        code_verifier=row.code_verifier,
        browser_token=row.browser_token,
        return_to=row.return_to,
    )
