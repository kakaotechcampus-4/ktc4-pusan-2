"""인증 비즈니스 로직. 트랜잭션 경계는 여기다.

controller 는 HTTP(쿠키·리다이렉트)만 다루고, google.py 는 구글만 알고,
DB 를 아는 것은 이 파일과 repository 뿐이다.

토큰 두 종류의 역할이 다르다.
- Access(JWT): 매 요청 인증. DB 를 보지 않는 대신 폐기할 수 없어 15분.
- Refresh(난수): Access 재발급. DB 조회로 즉시 폐기할 수 있어 14일.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

import redis
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from pitch_coach_backend.core import security
from pitch_coach_backend.core.config import settings
from pitch_coach_backend.module.auth import google, repository, state_store
from pitch_coach_backend.module.auth.exception import (
    EmailAlreadyRegistered,
    InvalidAuthorizationRequest,
    InvalidRefreshToken,
    RefreshTokenReused,
)
from pitch_coach_backend.module.auth.state_store import AuthorizationRequest
from pitch_coach_backend.module.user import service as user_service
from pitch_coach_backend.module.user.entity import User


@dataclass(frozen=True, slots=True)
class LoginStart:
    authorization_url: str
    state: str
    browser_token: str


@dataclass(frozen=True, slots=True)
class IssuedSession:
    """로그인·회전의 결과. controller 가 이걸로 쿠키와 응답을 만든다."""

    user: User
    access_token: str
    refresh_token: str
    device_id: uuid.UUID
    return_to: str = "/"


# --- 로그인 시작 ---------------------------------------------------------


def start_login(redis_client: redis.Redis, *, return_to: str) -> LoginStart:
    """state·nonce·PKCE 를 만들어 Redis 에 두고 구글 인증 URL 을 돌려준다.

    browser_token 은 임시 쿠키로 브라우저에 심는다. state 를 훔쳐도 이 쿠키가 없으면
    콜백을 완성할 수 없다 (state 단독보다 한 겹 두껍다).
    """
    state = state_store.new_state()
    nonce = state_store.new_nonce()
    browser_token = state_store.new_browser_token()
    verifier, challenge = google.build_pkce_pair()

    state_store.save(
        redis_client,
        state,
        AuthorizationRequest(
            nonce=nonce,
            code_verifier=verifier,
            browser_token=browser_token,
            return_to=return_to,
        ),
    )
    return LoginStart(
        authorization_url=google.build_authorization_url(
            state=state, nonce=nonce, code_challenge=challenge
        ),
        state=state,
        browser_token=browser_token,
    )


# --- 콜백 ---------------------------------------------------------------


def complete_login(
    db: Session,
    redis_client: redis.Redis,
    *,
    code: str,
    state: str,
    browser_token: str | None,
) -> IssuedSession:
    """콜백 처리 전체. 검증 -> 사용자 확보 -> 세션 발급 순서다."""
    pending = state_store.consume(redis_client, state)
    if pending is None:
        # 없거나 이미 소비됐거나 TTL 이 지났다. 셋을 구분해 알려주지 않는다.
        raise InvalidAuthorizationRequest()

    # 로그인을 시작한 브라우저가 맞는지. compare_digest 로 타이밍 차이를 없앤다.
    if not security.constant_time_equals(browser_token, pending.browser_token):
        raise InvalidAuthorizationRequest()

    id_token = google.exchange_code_for_id_token(code=code, code_verifier=pending.code_verifier)
    identity = google.verify_id_token(id_token, expected_nonce=pending.nonce)

    user = _find_or_create_user(db, identity)
    session = _issue_session(db, user=user, device_id=uuid.uuid7(), return_to=pending.return_to)
    db.commit()
    return session


def _find_or_create_user(db: Session, identity: google.GoogleIdentity) -> User:
    """(google, sub) 로 찾고 없으면 만든다. 사용자 식별은 이메일이 아니라 sub 다."""
    account = repository.get_account(db, provider=repository.GOOGLE, subject=identity.subject)
    if account is not None:
        # 재로그인. 구글에서 이름·이메일이 바뀌었어도 계정은 그대로다.
        return user_service.get(db, account.user_id)

    # 최초 로그인. 같은 이메일의 계정이 이미 있으면 자동으로 붙이지 않는다.
    # 구글의 email_verified 는 외부 주소의 현재 소유권까지 보장하지 않는다.
    if user_service.find_by_email(db, identity.email) is not None:
        raise EmailAlreadyRegistered()

    try:
        with db.begin_nested():
            user = user_service.create(db, email=identity.email, name=identity.name)
            repository.create_account(
                db,
                user_id=user.id,
                provider=repository.GOOGLE,
                subject=identity.subject,
                email=identity.email,
            )
    except IntegrityError:
        # 같은 사용자의 첫 로그인이 동시에 두 번 들어왔다.
        # UNIQUE(provider, provider_subject) 가 한쪽을 막았으니 이긴 쪽을 다시 읽는다.
        account = repository.get_account(db, provider=repository.GOOGLE, subject=identity.subject)
        if account is None:
            # sub 충돌이 아니라 email 충돌이었다. 자동 연결하지 않는 정책 그대로 막는다.
            raise EmailAlreadyRegistered() from None
        return user_service.get(db, account.user_id)

    return user


# --- 세션 발급·회전 ------------------------------------------------------


def _issue_session(
    db: Session, *, user: User, device_id: uuid.UUID, return_to: str = "/"
) -> IssuedSession:
    """Refresh 난수를 만들어 해시만 저장하고, 원문은 반환값으로만 내보낸다."""
    refresh_token = security.create_refresh_token()
    repository.create_refresh_token(
        db,
        user_id=user.id,
        token_hash=security.hash_refresh_token(refresh_token),
        device_id=device_id,
        expires_at=security.refresh_token_expires_at(),
    )
    return IssuedSession(
        user=user,
        access_token=security.create_access_token(user.id),
        refresh_token=refresh_token,
        device_id=device_id,
        return_to=return_to,
    )


def rotate(db: Session, refresh_token: str | None) -> IssuedSession:
    """Refresh 토큰 1회 교환. 쓴 토큰은 폐기하고 새 토큰을 발급한다.

    회전하는 이유: 토큰이 유출돼도 정상 사용자가 한 번 갱신하는 순간
    유출본이 무효가 되고, 공격자가 먼저 쓰면 아래 재사용 탐지에 걸린다.
    """
    if not refresh_token:
        raise InvalidRefreshToken()

    stored = repository.get_refresh_token(db, security.hash_refresh_token(refresh_token))
    if stored is None:
        raise InvalidRefreshToken()

    if stored.revoked_at is not None:
        # 이미 회전된 토큰이 다시 들어왔다. 정상 흐름에서는 일어나지 않는다.
        # 유출로 보고 이 세션의 살아 있는 토큰을 전부 끊는다 (공격자·피해자 양쪽 로그아웃).
        repository.revoke_device(db, user_id=stored.user_id, device_id=stored.device_id)
        db.commit()
        raise RefreshTokenReused()

    if stored.expires_at <= datetime.now(UTC):
        repository.revoke(db, stored)
        db.commit()
        raise InvalidRefreshToken()

    user = user_service.find(db, stored.user_id)
    if user is None:
        # 보통은 여기까지 오지 않는다. FK 의 ON DELETE CASCADE 가 탈퇴 시점에
        # 토큰 행까지 지우므로 위에서 이미 걸린다. FK 정책이 바뀌었을 때
        # 500 대신 401 로 막기 위한 방어선이다.
        repository.revoke(db, stored)
        db.commit()
        raise InvalidRefreshToken()

    # 폐기와 신규 발급이 한 트랜잭션이다. 중간에 실패하면 둘 다 없던 일이 된다.
    repository.revoke(db, stored)
    session = _issue_session(db, user=user, device_id=stored.device_id)
    db.commit()
    return session


def logout(db: Session, refresh_token: str | None) -> None:
    """현재 세션의 Refresh 를 폐기한다.

    이미 발급된 Access 는 최대 15분 살아 있다. 즉시 무효화는 이번 범위가 아니다.
    토큰이 없거나 모르는 값이어도 성공으로 응답한다 — 로그아웃은 멱등해야 하고,
    실패를 알려주면 토큰 존재 여부를 확인하는 통로가 된다.
    """
    if not refresh_token:
        return

    stored = repository.get_refresh_token(db, security.hash_refresh_token(refresh_token))
    if stored is None:
        return

    repository.revoke_device(db, user_id=stored.user_id, device_id=stored.device_id)
    db.commit()


def refresh_cookie_max_age() -> int:
    return settings.refresh_token_expire_days * 24 * 60 * 60
