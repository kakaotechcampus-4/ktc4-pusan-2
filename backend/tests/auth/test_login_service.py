"""auth service: 로그인 시작 · state · 콜백 검증 · 가입 · 세션 발급."""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from pitch_coach_backend.core import security
from pitch_coach_backend.module.auth import google, repository, service, state_store
from pitch_coach_backend.module.auth.entity import (
    OAuthAccount,
    OAuthAuthorizationRequest,
    RefreshToken,
)
from pitch_coach_backend.module.auth.exception import (
    EmailAlreadyRegistered,
    GoogleTokenExchangeFailed,
    InvalidAuthorizationRequest,
)
from pitch_coach_backend.module.user.entity import User
from tests.auth.conftest import _state_from, login

# --- 로그인 시작 ---------------------------------------------------------


def test_start_login_stores_the_request_and_returns_google_url(db_session: Session) -> None:
    started = service.start_login(db_session, return_to="/dashboard")

    assert started.authorization_url.startswith(google.AUTHORIZATION_ENDPOINT)
    pending = state_store.consume(db_session, started.state)
    assert pending is not None
    assert pending.browser_token == started.browser_token
    assert pending.return_to == "/dashboard"


def test_each_start_uses_a_fresh_state(db_session: Session) -> None:
    first = service.start_login(db_session, return_to="/")
    second = service.start_login(db_session, return_to="/")

    assert first.state != second.state
    assert first.browser_token != second.browser_token


# --- 콜백 검증 -----------------------------------------------------------


def test_unknown_state_is_rejected(db_session: Session, fake_google: dict[str, Any]) -> None:
    with pytest.raises(InvalidAuthorizationRequest):
        service.complete_login(db_session, code="c", state="never-issued", browser_token="x")


def test_state_cannot_be_replayed(db_session: Session, fake_google: dict[str, Any]) -> None:
    started = service.start_login(db_session, return_to="/")
    state = _state_from(started.authorization_url)
    service.complete_login(db_session, code="c", state=state, browser_token=started.browser_token)

    with pytest.raises(InvalidAuthorizationRequest):
        service.complete_login(
            db_session, code="c", state=state, browser_token=started.browser_token
        )


def test_stolen_state_without_browser_cookie_is_rejected(
    db_session: Session, fake_google: dict[str, Any]
) -> None:
    """공격자가 state 를 알아내도 임시 쿠키가 없으면 로그인을 완성하지 못한다."""
    started = service.start_login(db_session, return_to="/")
    state = _state_from(started.authorization_url)

    with pytest.raises(InvalidAuthorizationRequest):
        service.complete_login(db_session, code="c", state=state, browser_token="wrong-token")


def test_missing_browser_cookie_is_rejected(
    db_session: Session, fake_google: dict[str, Any]
) -> None:
    started = service.start_login(db_session, return_to="/")
    state = _state_from(started.authorization_url)

    with pytest.raises(InvalidAuthorizationRequest):
        service.complete_login(db_session, code="c", state=state, browser_token=None)


def test_expired_request_is_rejected(db_session: Session, fake_google: dict[str, Any]) -> None:
    started = service.start_login(db_session, return_to="/")
    state = _state_from(started.authorization_url)
    db_session.execute(
        update(OAuthAuthorizationRequest)
        .where(OAuthAuthorizationRequest.state == state)
        .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
    )

    with pytest.raises(InvalidAuthorizationRequest):
        service.complete_login(
            db_session, code="c", state=state, browser_token=started.browser_token
        )
    assert fake_google["exchanged"] == []


def test_state_is_used_up_even_when_google_exchange_fails(
    db_session: Session, fake_google: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """요청을 지운 커밋은 뒤의 실패로 되돌아가지 않는다. 같은 state 로 다시 시도할 수 없다."""
    started = service.start_login(db_session, return_to="/")
    state = _state_from(started.authorization_url)

    def fail(*, code: str, code_verifier: str) -> str:
        raise GoogleTokenExchangeFailed()

    with monkeypatch.context() as patch:
        patch.setattr(service.google, "exchange_code_for_id_token", fail)
        with pytest.raises(GoogleTokenExchangeFailed):
            service.complete_login(
                db_session, code="c", state=state, browser_token=started.browser_token
            )
    # 요청이 끝나면 get_db 가 세션을 닫으며 남은 트랜잭션을 되돌린다
    db_session.rollback()

    with pytest.raises(InvalidAuthorizationRequest):
        service.complete_login(
            db_session, code="c", state=state, browser_token=started.browser_token
        )


def test_state_is_used_up_even_when_signup_is_refused(
    db_session: Session, fake_google: dict[str, Any]
) -> None:
    login(db_session)
    fake_google["identity"] = google.GoogleIdentity(
        subject="sub-2", email="new@example.com", name="다른사람"
    )
    started = service.start_login(db_session, return_to="/")
    state = _state_from(started.authorization_url)

    with pytest.raises(EmailAlreadyRegistered):
        service.complete_login(
            db_session, code="c", state=state, browser_token=started.browser_token
        )
    db_session.rollback()

    with pytest.raises(InvalidAuthorizationRequest):
        service.complete_login(
            db_session, code="c", state=state, browser_token=started.browser_token
        )


def test_cancelled_login_cannot_be_completed(
    db_session: Session, fake_google: dict[str, Any]
) -> None:
    """동의 화면에서 취소하고 돌아온 state 는 지운다."""
    started = service.start_login(db_session, return_to="/")
    state = _state_from(started.authorization_url)

    service.cancel_login(db_session, state)

    with pytest.raises(InvalidAuthorizationRequest):
        service.complete_login(
            db_session, code="c", state=state, browser_token=started.browser_token
        )


def test_callback_passes_the_stored_nonce_and_verifier_to_google(
    db_session: Session, fake_google: dict[str, Any]
) -> None:
    started = service.start_login(db_session, return_to="/")
    state = _state_from(started.authorization_url)
    pending = state_store.consume(db_session, state)
    assert pending is not None
    # consume 이 지웠으니 되돌려 놓는다
    state_store.save(db_session, state, pending)

    service.complete_login(
        db_session, code="the-code", state=state, browser_token=started.browser_token
    )

    assert fake_google["exchanged"][0]["code"] == "the-code"
    assert fake_google["exchanged"][0]["code_verifier"] == pending.code_verifier
    assert fake_google["nonce_seen"] == pending.nonce


# --- 가입·재로그인 -------------------------------------------------------


def test_first_login_creates_user_and_oauth_account(
    db_session: Session, fake_google: dict[str, Any]
) -> None:
    issued = login(db_session)

    assert issued.user.email == "new@example.com"
    assert issued.user.name == "새사용자"
    account = db_session.scalar(select(OAuthAccount))
    assert account is not None
    assert account.user_id == issued.user.id
    assert account.provider == "google"
    assert account.provider_subject == "sub-1"


def test_long_google_name_is_cut_to_fit_the_column(
    db_session: Session, fake_google: dict[str, Any]
) -> None:
    """구글 이름에는 길이 제한이 없다. 그대로 넣으면 DataError 로 로그인이 실패한다."""
    fake_google["identity"] = google.GoogleIdentity(
        subject="sub-1", email="new@example.com", name="가" * 150
    )

    issued = login(db_session)

    assert issued.user.name == "가" * 100


def test_second_login_reuses_the_same_user(
    db_session: Session, fake_google: dict[str, Any]
) -> None:
    first = login(db_session)
    second = login(db_session)

    assert first.user.id == second.user.id
    assert db_session.scalars(select(User)).all() != []
    assert len(db_session.scalars(select(User)).all()) == 1
    assert len(db_session.scalars(select(OAuthAccount)).all()) == 1


def test_user_is_identified_by_subject_not_email(
    db_session: Session, fake_google: dict[str, Any]
) -> None:
    """구글에서 이메일을 바꿔도 sub 가 같으면 같은 계정이다."""
    first = login(db_session)

    fake_google["identity"] = google.GoogleIdentity(
        subject="sub-1", email="changed@example.com", name="이름바뀜"
    )
    second = login(db_session)

    assert first.user.id == second.user.id


def test_same_email_with_different_subject_is_refused(
    db_session: Session, fake_google: dict[str, Any]
) -> None:
    """이메일이 같아도 다른 구글 계정을 기존 사용자에 자동으로 붙이지 않는다."""
    login(db_session)

    fake_google["identity"] = google.GoogleIdentity(
        subject="sub-2", email="new@example.com", name="다른사람"
    )
    with pytest.raises(EmailAlreadyRegistered):
        login(db_session)

    assert len(db_session.scalars(select(User)).all()) == 1


def test_concurrent_first_login_creates_only_one_user(
    db_session: Session, fake_google: dict[str, Any]
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

    issued = login(db_session)

    assert issued.user.id == winner.id
    assert len(db_session.scalars(select(User)).all()) == 1


# --- 세션 발급 -----------------------------------------------------------


def test_login_issues_access_and_refresh_tokens(
    db_session: Session, fake_google: dict[str, Any]
) -> None:
    issued = login(db_session)

    payload = security.decode_access_token(issued.access_token)
    assert payload["sub"] == str(issued.user.id)
    assert issued.refresh_token


def test_refresh_token_is_stored_only_as_a_hash(
    db_session: Session, fake_google: dict[str, Any]
) -> None:
    issued = login(db_session)

    stored = db_session.scalar(select(RefreshToken))
    assert stored is not None
    assert stored.token_hash == security.hash_refresh_token(issued.refresh_token)
    assert issued.refresh_token not in stored.token_hash


def test_login_carries_return_to_through(db_session: Session, fake_google: dict[str, Any]) -> None:
    issued = login(db_session, return_to="/pitches/1")

    assert issued.return_to == "/pitches/1"


def test_each_login_starts_a_new_device_session(
    db_session: Session, fake_google: dict[str, Any]
) -> None:
    """다른 브라우저에서 로그인해도 기존 세션이 끊기지 않아야 한다."""
    first = login(db_session)
    second = login(db_session)

    assert first.device_id != second.device_id
    alive = db_session.scalars(select(RefreshToken).where(RefreshToken.revoked_at.is_(None))).all()
    assert len(alive) == 2
