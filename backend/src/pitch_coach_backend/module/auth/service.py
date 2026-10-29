"""인증 비즈니스 로직. 트랜잭션 경계는 여기다.

controller 는 HTTP(쿠키·리다이렉트)만 다루고, google.py 는 구글만 알고,
DB 를 아는 것은 이 파일과 repository 뿐이다.

토큰 두 종류의 역할이 다르다.
- Access(JWT): 매 요청 인증. DB 를 보지 않는 대신 폐기할 수 없어 15분.
- Refresh(난수): Access 재발급. DB 조회로 즉시 폐기할 수 있어 14일.
  회전할 때마다 14일이 다시 잡히지만 로그인한 지 30일이 지나면 끝난다.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import NoReturn

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from pitch_coach_backend.core import security
from pitch_coach_backend.core.config import settings
from pitch_coach_backend.module.auth import google, repository, state_store
from pitch_coach_backend.module.auth.entity import RefreshToken
from pitch_coach_backend.module.auth.exception import (
    EmailAlreadyRegistered,
    InvalidAuthorizationRequest,
    InvalidRefreshToken,
    RefreshTokenReused,
    ServiceNotOpen,
)
from pitch_coach_backend.module.auth.state_store import AuthorizationRequest
from pitch_coach_backend.module.user import service as user_service
from pitch_coach_backend.module.user.entity import NAME_MAX_LENGTH, User


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


# --- 출시 전 차단 -------------------------------------------------------


def ensure_launched() -> None:
    """출시 시각(LAUNCH_AT) 전이면 로그인을 막는다. 설정이 비어 있으면 막지 않는다.

    FE 는 기기 시각으로 시작 버튼만 막으므로 주소를 직접 열면 넘어올 수 있다. 실제로 막는 곳은
    여기다. 사용자·세션은 콜백에서만 생기므로 시작과 콜백 두 곳만 막으면 새로 들어오는 길은
    없다. 이미 발급된 세션(refresh·보호 API·WS)은 막지 않는다.
    """
    if settings.launch_at is not None and datetime.now(UTC) < settings.launch_at:
        raise ServiceNotOpen()


# --- 로그인 시작 ---------------------------------------------------------


def start_login(db: Session, *, return_to: str) -> LoginStart:
    """state·nonce·PKCE 를 만들어 DB 에 두고 구글 인증 URL 을 돌려준다.

    browser_token 은 임시 쿠키로 브라우저에 심는다. state 를 훔쳐도 이 쿠키가 없으면
    콜백을 완성할 수 없다 (state 단독보다 한 겹 두껍다).
    """
    ensure_launched()
    state = state_store.new_state()
    nonce = state_store.new_nonce()
    browser_token = state_store.new_browser_token()
    verifier, challenge = google.build_pkce_pair()

    state_store.save(
        db,
        state,
        AuthorizationRequest(
            nonce=nonce,
            code_verifier=verifier,
            browser_token=browser_token,
            return_to=return_to,
        ),
    )
    db.commit()
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
    *,
    code: str,
    state: str,
    browser_token: str | None,
) -> IssuedSession:
    """콜백 처리 전체. 검증 -> 사용자 확보 -> 세션 발급 순서다.

    인증 요청은 꺼내자마자 커밋해 지운다. 뒤에서 브라우저 확인·구글 통신·사용자 저장이
    실패해 롤백돼도 같은 state 를 다시 쓸 수 없다. 커밋하면 연결을 돌려주므로 구글 응답을
    기다리는 동안 트랜잭션을 열어 두지도 않는다.
    """
    pending = state_store.consume(db, state)
    db.commit()
    if pending is None:
        # 없거나 이미 소비됐거나 만료됐다. 셋을 구분해 알려주지 않는다.
        raise InvalidAuthorizationRequest()

    # 로그인을 시작한 브라우저가 맞는지. compare_digest 로 타이밍 차이를 없앤다.
    if not security.constant_time_equals(browser_token, pending.browser_token):
        raise InvalidAuthorizationRequest()

    # 시작에서 막으니 보통은 여기까지 오지 않는다. 막기 전에 시작해 둔 요청(state 는 10분 산다)이
    # 출시 전에 들어오는 경우다. 구글과 통신하거나 사용자를 만들기 전에 끊는다
    ensure_launched()

    id_token = google.exchange_code_for_id_token(code=code, code_verifier=pending.code_verifier)
    identity = google.verify_id_token(id_token, expected_nonce=pending.nonce)

    user = _find_or_create_user(db, identity)
    session = _issue_session(db, user=user, device_id=uuid.uuid7(), return_to=pending.return_to)
    db.commit()
    return session


def cancel_login(db: Session, state: str) -> None:
    """동의 화면에서 취소하는 등 code 없이 돌아온 요청을 지운다. 만료로도 거절되지만 바로 지운다."""
    state_store.consume(db, state)
    db.commit()


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
            # 구글 이름은 길이 제한이 없다. 컬럼보다 길면 INSERT 가 DataError 로 실패한다.
            user = user_service.create(
                db, email=identity.email, name=identity.name[:NAME_MAX_LENGTH]
            )
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
    db: Session,
    *,
    user: User,
    device_id: uuid.UUID,
    return_to: str = "/",
    replaces: RefreshToken | None = None,
) -> IssuedSession:
    """Refresh 난수를 만들어 해시만 저장하고, 원문은 반환값으로만 내보낸다.

    replaces 는 회전으로 폐기한 토큰이다. 새 토큰을 거기에 이어 두고 세션의 끝도 이어받는다.
    만료는 지금부터 14일과 세션의 끝 중 이른 쪽이다.
    """
    if replaces is None:
        session_expires_at = security.refresh_session_expires_at()
    else:
        session_expires_at = replaces.session_expires_at
    refresh_token = security.create_refresh_token()
    issued = repository.create_refresh_token(
        db,
        user_id=user.id,
        token_hash=security.hash_refresh_token(refresh_token),
        device_id=device_id,
        expires_at=min(security.refresh_token_expires_at(), session_expires_at),
        session_expires_at=session_expires_at,
    )
    if replaces is not None:
        replaces.replaced_by_id = issued.id
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

    # 같은 기기의 회전·로그아웃을 하나씩 처리한다. 토큰이 회전해 행이 달라져도 같은 잠금이다.
    # 유예 재회전과 재사용 탐지도 커밋까지 이 잠금을 유지한다.
    stored = repository.get_refresh_token_for_update(db, security.hash_refresh_token(refresh_token))
    if stored is None:
        raise InvalidRefreshToken()

    if stored.revoked_at is not None:
        # 이미 폐기된 토큰이 다시 들어왔다
        return _rotate_again(db, stored)

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
    # 행을 잠갔으니 읽은 뒤 다른 요청이 먼저 폐기하는 일은 없어야 한다. 그래도 그렇게 되면
    # 둘 다 새 토큰을 받아 계보가 갈라진다. 조건부 폐기로 한 요청만 이기게 하고, 진 쪽은
    # 이미 폐기된 토큰이 들어온 것과 같게 다룬다
    if not repository.revoke_if_active(db, stored.id):
        db.refresh(stored)
        return _rotate_again(db, stored)
    session = _issue_session(db, user=user, device_id=stored.device_id, replaces=stored)
    db.commit()
    return session


def _rotate_again(db: Session, stored: RefreshToken) -> IssuedSession:
    """이미 폐기된 토큰을 받았다. 방금 회전된 토큰이면 한 번 더 회전하고, 아니면 재사용이다.

    회전 응답을 받기 전에 새로고침한 브라우저는 새 쿠키를 버리고 옛 토큰을 다시 보낸다.
    회전한 지 얼마 안 됐고 그때 이어받은 토큰이 아직 안 쓰였으면, 그 토큰을 폐기하고 새로 하나
    이어 준다. 기기당 살아 있는 토큰은 계속 하나다. 탈취한 쪽이 이 틈에 끼어들면 정상 사용자가
    밀려나고, 그 사용자가 다음에 갱신할 때 폐기된 토큰을 내게 되어 재사용으로 걸린다.

    한계: 밀려난 사용자가 다시 갱신하러 와야 걸린다. 돌아오지 않으면 탈취한 쪽은 Refresh 가
    만료되거나(기본 14일, 로그인 뒤 최대 30일) 로그아웃할 때까지 세션을 이어 간다.
    이 틈은 회전 직후 유예 시간 안에 옛 토큰을 쓴 경우로 좁고, 응답을 못 받은 새로고침을
    로그아웃시키지 않는 쪽을 택했다.
    """
    assert stored.revoked_at is not None
    grace = timedelta(seconds=settings.refresh_token_reuse_grace_seconds)
    if (
        # 로그아웃·재사용 탐지로 폐기됐다
        stored.replaced_by_id is None
        or datetime.now(UTC) - stored.revoked_at > grace
        # 이어받은 토큰이 이미 쓰였거나 폐기됐다
        or not repository.revoke_if_active(db, stored.replaced_by_id)
    ):
        _reject_reuse(db, stored)

    user = user_service.get(db, stored.user_id)
    session = _issue_session(db, user=user, device_id=stored.device_id, replaces=stored)
    db.commit()
    return session


def _reject_reuse(db: Session, stored: RefreshToken) -> NoReturn:
    """유출로 보고 이 세션의 살아 있는 토큰을 전부 끊는다 (공격자·피해자 양쪽 로그아웃)."""
    repository.revoke_device(db, user_id=stored.user_id, device_id=stored.device_id)
    db.commit()
    raise RefreshTokenReused()


def logout(db: Session, refresh_token: str | None) -> uuid.UUID | None:
    """현재 세션의 Refresh 를 폐기하고 그 사용자를 돌려준다. 폐기할 세션이 없으면 None.

    이미 발급된 Access 는 최대 15분 살아 있다. 즉시 무효화는 이번 범위가 아니다.
    토큰이 없거나 모르는 값이어도 성공으로 응답한다 — 로그아웃은 멱등해야 하고,
    실패를 알려주면 토큰 존재 여부를 확인하는 통로가 된다.
    """
    if not refresh_token:
        return None

    # 먼저 진행하던 회전이 커밋된 뒤 폐기를 시작해야 새로 발급된 토큰도 함께 폐기한다.
    stored = repository.get_refresh_token_for_update(db, security.hash_refresh_token(refresh_token))
    if stored is None:
        return None

    revoked = repository.revoke_device(db, user_id=stored.user_id, device_id=stored.device_id)
    db.commit()
    # 이미 로그아웃한 토큰으로 다른 기기의 발표 연결까지 반복해서 끊지 않는다.
    # 회전 전 토큰이어도 살아 있는 후손을 폐기했다면 연결 종료를 알린다.
    return stored.user_id if revoked else None


def refresh_cookie_max_age() -> int:
    return settings.refresh_token_expire_days * 24 * 60 * 60
