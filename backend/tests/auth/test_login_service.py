"""auth service: 로그인 시작 · state · 콜백 검증 · 가입 · 세션 발급."""

from typing import Any

import pytest
import redis
from sqlalchemy import select
from sqlalchemy.orm import Session

from pitch_coach_backend.core import security
from pitch_coach_backend.module.auth import google, repository, service, state_store
from pitch_coach_backend.module.auth.entity import OAuthAccount, RefreshToken
from pitch_coach_backend.module.auth.exception import (
    EmailAlreadyRegistered,
    InvalidAuthorizationRequest,
)
from pitch_coach_backend.module.user.entity import User
from tests.auth.conftest import _state_from, login

# --- 로그인 시작 ---------------------------------------------------------


def test_start_login_stores_the_request_and_returns_google_url(
    redis_client: redis.Redis,
) -> None:
    started = service.start_login(redis_client, return_to="/dashboard")

    assert started.authorization_url.startswith(google.AUTHORIZATION_ENDPOINT)
    pending = state_store.consume(redis_client, started.state)
    assert pending is not None
    assert pending.browser_token == started.browser_token
    assert pending.return_to == "/dashboard"


def test_each_start_uses_a_fresh_state(redis_client: redis.Redis) -> None:
    first = service.start_login(redis_client, return_to="/")
    second = service.start_login(redis_client, return_to="/")

    assert first.state != second.state
    assert first.browser_token != second.browser_token


# --- 콜백 검증 -----------------------------------------------------------


def test_unknown_state_is_rejected(
    db_session: Session, redis_client: redis.Redis, fake_google: dict[str, Any]
) -> None:
    with pytest.raises(InvalidAuthorizationRequest):
        service.complete_login(
            db_session, redis_client, code="c", state="never-issued", browser_token="x"
        )


def test_state_cannot_be_replayed(
    db_session: Session, redis_client: redis.Redis, fake_google: dict[str, Any]
) -> None:
    started = service.start_login(redis_client, return_to="/")
    state = _state_from(started.authorization_url)
    service.complete_login(
        db_session, redis_client, code="c", state=state, browser_token=started.browser_token
    )

    with pytest.raises(InvalidAuthorizationRequest):
        service.complete_login(
            db_session, redis_client, code="c", state=state, browser_token=started.browser_token
        )


def test_stolen_state_without_browser_cookie_is_rejected(
    db_session: Session, redis_client: redis.Redis, fake_google: dict[str, Any]
) -> None:
    """공격자가 state 를 알아내도 임시 쿠키가 없으면 로그인을 완성하지 못한다."""
    started = service.start_login(redis_client, return_to="/")
    state = _state_from(started.authorization_url)

    with pytest.raises(InvalidAuthorizationRequest):
        service.complete_login(
            db_session, redis_client, code="c", state=state, browser_token="wrong-token"
        )


def test_missing_browser_cookie_is_rejected(
    db_session: Session, redis_client: redis.Redis, fake_google: dict[str, Any]
) -> None:
    started = service.start_login(redis_client, return_to="/")
    state = _state_from(started.authorization_url)

    with pytest.raises(InvalidAuthorizationRequest):
        service.complete_login(db_session, redis_client, code="c", state=state, browser_token=None)


def test_callback_passes_the_stored_nonce_and_verifier_to_google(
    db_session: Session, redis_client: redis.Redis, fake_google: dict[str, Any]
) -> None:
    started = service.start_login(redis_client, return_to="/")
    state = _state_from(started.authorization_url)
    pending = state_store.consume(redis_client, state)
    assert pending is not None
    # consume 이 지웠으니 되돌려 놓는다
    state_store.save(redis_client, state, pending)

    service.complete_login(
        db_session, redis_client, code="the-code", state=state, browser_token=started.browser_token
    )

    assert fake_google["exchanged"][0]["code"] == "the-code"
    assert fake_google["exchanged"][0]["code_verifier"] == pending.code_verifier
    assert fake_google["nonce_seen"] == pending.nonce


# --- 가입·재로그인 -------------------------------------------------------


def test_first_login_creates_user_and_oauth_account(
    db_session: Session, redis_client: redis.Redis, fake_google: dict[str, Any]
) -> None:
    issued = login(db_session, redis_client)

    assert issued.user.email == "new@example.com"
    assert issued.user.name == "새사용자"
    account = db_session.scalar(select(OAuthAccount))
    assert account is not None
    assert account.user_id == issued.user.id
    assert account.provider == "google"
    assert account.provider_subject == "sub-1"


def test_long_google_name_is_cut_to_fit_the_column(
    db_session: Session, redis_client: redis.Redis, fake_google: dict[str, Any]
) -> None:
    """구글 이름에는 길이 제한이 없다. 그대로 넣으면 DataError 로 로그인이 실패한다."""
    fake_google["identity"] = google.GoogleIdentity(
        subject="sub-1", email="new@example.com", name="가" * 150
    )

    issued = login(db_session, redis_client)

    assert issued.user.name == "가" * 100


def test_second_login_reuses_the_same_user(
    db_session: Session, redis_client: redis.Redis, fake_google: dict[str, Any]
) -> None:
    first = login(db_session, redis_client)
    second = login(db_session, redis_client)

    assert first.user.id == second.user.id
    assert db_session.scalars(select(User)).all() != []
    assert len(db_session.scalars(select(User)).all()) == 1
    assert len(db_session.scalars(select(OAuthAccount)).all()) == 1


def test_user_is_identified_by_subject_not_email(
    db_session: Session, redis_client: redis.Redis, fake_google: dict[str, Any]
) -> None:
    """구글에서 이메일을 바꿔도 sub 가 같으면 같은 계정이다."""
    first = login(db_session, redis_client)

    fake_google["identity"] = google.GoogleIdentity(
        subject="sub-1", email="changed@example.com", name="이름바뀜"
    )
    second = login(db_session, redis_client)

    assert first.user.id == second.user.id


def test_same_email_with_different_subject_is_refused(
    db_session: Session, redis_client: redis.Redis, fake_google: dict[str, Any]
) -> None:
    """이메일이 같아도 다른 구글 계정을 기존 사용자에 자동으로 붙이지 않는다."""
    login(db_session, redis_client)

    fake_google["identity"] = google.GoogleIdentity(
        subject="sub-2", email="new@example.com", name="다른사람"
    )
    with pytest.raises(EmailAlreadyRegistered):
        login(db_session, redis_client)

    assert len(db_session.scalars(select(User)).all()) == 1


def test_concurrent_first_login_creates_only_one_user(
    db_session: Session, redis_client: redis.Redis, fake_google: dict[str, Any]
) -> None:
    """UNIQUE(provider, subject) 로 경쟁이 걸린 뒤 이긴 쪽을 다시 읽어야 한다.

    다른 요청이 먼저 커밋한 상황을 직접 만들어 IntegrityError 경로를 실행한다.
    """
    identity = fake_google["identity"]
    winner = User(email=identity.email, name=identity.name)
    db_session.add(winner)
    db_session.flush()
    repository.create_account(
        db_session,
        user_id=winner.id,
        provider="google",
        subject=identity.subject,
        email=identity.email,
    )
    db_session.commit()

    issued = login(db_session, redis_client)

    assert issued.user.id == winner.id
    assert len(db_session.scalars(select(User)).all()) == 1


# --- 세션 발급 -----------------------------------------------------------


def test_login_issues_access_and_refresh_tokens(
    db_session: Session, redis_client: redis.Redis, fake_google: dict[str, Any]
) -> None:
    issued = login(db_session, redis_client)

    payload = security.decode_access_token(issued.access_token)
    assert payload["sub"] == str(issued.user.id)
    assert issued.refresh_token


def test_refresh_token_is_stored_only_as_a_hash(
    db_session: Session, redis_client: redis.Redis, fake_google: dict[str, Any]
) -> None:
    issued = login(db_session, redis_client)

    stored = db_session.scalar(select(RefreshToken))
    assert stored is not None
    assert stored.token_hash == security.hash_refresh_token(issued.refresh_token)
    assert issued.refresh_token not in stored.token_hash


def test_login_carries_return_to_through(
    db_session: Session, redis_client: redis.Redis, fake_google: dict[str, Any]
) -> None:
    issued = login(db_session, redis_client, return_to="/pitches/1")

    assert issued.return_to == "/pitches/1"


def test_each_login_starts_a_new_device_session(
    db_session: Session, redis_client: redis.Redis, fake_google: dict[str, Any]
) -> None:
    """다른 브라우저에서 로그인해도 기존 세션이 끊기지 않아야 한다."""
    first = login(db_session, redis_client)
    second = login(db_session, redis_client)

    assert first.device_id != second.device_id
    alive = db_session.scalars(select(RefreshToken).where(RefreshToken.revoked_at.is_(None))).all()
    assert len(alive) == 2
