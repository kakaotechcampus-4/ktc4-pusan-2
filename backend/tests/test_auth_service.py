"""auth service 테스트.

구글 통신(code 교환·ID Token 검증)만 가짜로 바꾸고 나머지는 실제로 돈다.
DB 는 진짜 트랜잭션, Redis 도 진짜다. 검증 로직을 통째로 패치하지 않는다.
"""

import threading
import uuid
from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlparse

import pytest
import redis
from sqlalchemy import Engine, select, text
from sqlalchemy.orm import Session

from pitch_coach_backend.core import security
from pitch_coach_backend.core.config import settings
from pitch_coach_backend.core.exceptions import UnauthorizedException
from pitch_coach_backend.module.auth import google, repository, service, state_store
from pitch_coach_backend.module.auth.entity import OAuthAccount, RefreshToken
from pitch_coach_backend.module.auth.exception import (
    EmailAlreadyRegistered,
    InvalidAuthorizationRequest,
    InvalidRefreshToken,
    RefreshTokenReused,
)
from pitch_coach_backend.module.user.entity import User

TEST_REDIS_DB = 15


@pytest.fixture
def redis_client() -> Generator[redis.Redis]:
    from pitch_coach_backend.core.config import settings

    url = urlparse(settings.redis_url)._replace(path=f"/{TEST_REDIS_DB}").geturl()
    client = redis.Redis.from_url(url, decode_responses=True)
    client.flushdb()
    yield client
    client.flushdb()
    client.close()


@pytest.fixture
def fake_google(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """구글 왕복만 가짜로. identity 를 바꿔가며 시나리오를 만든다."""
    box: dict[str, Any] = {
        "identity": google.GoogleIdentity(
            subject="sub-1", email="new@example.com", name="새사용자"
        ),
        "exchanged": [],
    }

    def fake_exchange(*, code: str, code_verifier: str) -> str:
        box["exchanged"].append({"code": code, "code_verifier": code_verifier})
        return "signed-by-google"

    def fake_verify(id_token: str, *, expected_nonce: str) -> google.GoogleIdentity:
        box["nonce_seen"] = expected_nonce
        return box["identity"]

    monkeypatch.setattr(service.google, "exchange_code_for_id_token", fake_exchange)
    monkeypatch.setattr(service.google, "verify_id_token", fake_verify)
    return box


def login(db: Session, redis_client: redis.Redis, *, return_to: str = "/") -> service.IssuedSession:
    """start -> callback 을 한 번 돌린다. 실제 흐름 그대로 state 를 주고받는다."""
    started = service.start_login(redis_client, return_to=return_to)
    state = _state_from(started.authorization_url)
    return service.complete_login(
        db,
        redis_client,
        code="auth-code",
        state=state,
        browser_token=started.browser_token,
    )


def _past_grace(db: Session, refresh_token: str) -> None:
    """이 토큰을 회전한 지 유예 시간이 지난 것으로 만든다."""
    db.execute(
        text(
            "UPDATE refresh_tokens SET revoked_at = revoked_at - make_interval(secs => :secs)"
            " WHERE token_hash = :token_hash"
        ),
        {
            "secs": settings.refresh_token_reuse_grace_seconds + 1,
            "token_hash": security.hash_refresh_token(refresh_token),
        },
    )


def _alive(db: Session, device_id: uuid.UUID) -> list[str]:
    """이 기기에서 살아 있는 토큰의 해시."""
    return list(
        db.scalars(
            select(RefreshToken.token_hash).where(
                RefreshToken.device_id == device_id, RefreshToken.revoked_at.is_(None)
            )
        )
    )


def _state_from(url: str) -> str:
    from urllib.parse import parse_qs

    return parse_qs(urlparse(url).query)["state"][0]


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


# --- 회전 ---------------------------------------------------------------


def test_rotate_returns_new_tokens(
    db_session: Session, redis_client: redis.Redis, fake_google: dict[str, Any]
) -> None:
    issued = login(db_session, redis_client)

    rotated = service.rotate(db_session, issued.refresh_token)

    assert rotated.user.id == issued.user.id
    assert rotated.refresh_token != issued.refresh_token
    # 같은 브라우저 세션이 이어진다
    assert rotated.device_id == issued.device_id


def test_rotate_revokes_the_used_token(
    db_session: Session, redis_client: redis.Redis, fake_google: dict[str, Any]
) -> None:
    issued = login(db_session, redis_client)
    service.rotate(db_session, issued.refresh_token)

    old = repository.get_refresh_token(
        db_session, security.hash_refresh_token(issued.refresh_token)
    )
    assert old is not None
    assert old.revoked_at is not None


def test_rotate_links_the_used_token_to_the_new_one(
    db_session: Session, redis_client: redis.Redis, fake_google: dict[str, Any]
) -> None:
    issued = login(db_session, redis_client)
    rotated = service.rotate(db_session, issued.refresh_token)

    old = repository.get_refresh_token(
        db_session, security.hash_refresh_token(issued.refresh_token)
    )
    new = repository.get_refresh_token(
        db_session, security.hash_refresh_token(rotated.refresh_token)
    )
    assert old is not None and new is not None
    assert old.replaced_by_id == new.id
    assert new.replaced_by_id is None


def test_rotated_token_cannot_be_used_again(
    db_session: Session, redis_client: redis.Redis, fake_google: dict[str, Any]
) -> None:
    issued = login(db_session, redis_client)
    service.rotate(db_session, issued.refresh_token)
    _past_grace(db_session, issued.refresh_token)

    with pytest.raises(RefreshTokenReused):
        service.rotate(db_session, issued.refresh_token)


def test_reuse_kills_the_whole_device_session(
    db_session: Session, redis_client: redis.Redis, fake_google: dict[str, Any]
) -> None:
    """유출로 보고 공격자와 정상 사용자 양쪽을 모두 로그아웃시킨다."""
    issued = login(db_session, redis_client)
    current = service.rotate(db_session, issued.refresh_token)
    _past_grace(db_session, issued.refresh_token)

    with pytest.raises(RefreshTokenReused):
        service.rotate(db_session, issued.refresh_token)

    # 방금까지 멀쩡하던 최신 토큰도 함께 끊긴다
    with pytest.raises(UnauthorizedException):
        service.rotate(db_session, current.refresh_token)


def test_reuse_does_not_touch_other_devices(
    db_session: Session, redis_client: redis.Redis, fake_google: dict[str, Any]
) -> None:
    """한 브라우저가 털려도 다른 기기의 세션까지 끊지는 않는다."""
    laptop = login(db_session, redis_client)
    phone = login(db_session, redis_client)
    service.rotate(db_session, laptop.refresh_token)
    _past_grace(db_session, laptop.refresh_token)

    with pytest.raises(RefreshTokenReused):
        service.rotate(db_session, laptop.refresh_token)

    assert service.rotate(db_session, phone.refresh_token).user.id == phone.user.id


def test_token_revoked_after_it_was_read_is_rejected(
    db_session: Session,
    redis_client: redis.Redis,
    fake_google: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """읽은 뒤 쓰기 전에 로그아웃이 먼저 폐기했다. 새 토큰을 내주면 끊은 세션이 되살아난다."""
    issued = login(db_session, redis_client)
    original_get = repository.get_refresh_token

    def get_then_lose_the_race(db: Session, token_hash: str) -> RefreshToken | None:
        token = original_get(db, token_hash)
        assert token is not None
        # 로그아웃이 이 사이에 커밋됐다. ORM 객체는 모른 채 revoked_at=None 으로 남는다
        db.execute(
            text("UPDATE refresh_tokens SET revoked_at = now() WHERE id = :id"),
            {"id": token.id},
        )
        return token

    monkeypatch.setattr(repository, "get_refresh_token", get_then_lose_the_race)

    with pytest.raises(RefreshTokenReused):
        service.rotate(db_session, issued.refresh_token)

    monkeypatch.undo()
    device_tokens = db_session.scalars(
        select(RefreshToken).where(RefreshToken.device_id == issued.device_id)
    ).all()
    # 진 쪽은 새 토큰을 받지 않고, 그 세션은 통째로 끊긴다
    assert len(device_tokens) == 1
    assert all(t.revoked_at is not None for t in device_tokens)


def test_concurrent_rotation_keeps_one_live_token(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """같은 Refresh 로 요청 둘이 동시에 온다 (회전 중에 새로고침했거나 탈취한 쪽과 겹친 경우).

    잠금이 없으면 둘 다 revoked_at IS NULL 을 보고 각자 새 토큰을 받아 계보가 갈라진다.
    늦은 쪽은 먼저 회전한 쪽의 새 토큰을 폐기하고 이어받아, 살아 있는 토큰은 하나만 남는다.
    트랜잭션 두 개가 실제로 겹쳐야 해서 테스트 세션(savepoint) 대신 진짜 세션 둘을 쓰고,
    만든 데이터는 지운다.
    """
    with Session(engine) as setup:
        user = User(email=f"{uuid.uuid4()}@example.com", name="동시 회전")
        setup.add(user)
        setup.flush()
        refresh_token = security.create_refresh_token()
        device_id = uuid.uuid7()
        repository.create_refresh_token(
            setup,
            user_id=user.id,
            token_hash=security.hash_refresh_token(refresh_token),
            device_id=device_id,
            expires_at=security.refresh_token_expires_at(),
        )
        setup.commit()
        user_id = user.id

    # 둘 다 토큰을 읽은 뒤에야 폐기로 넘어가게 해서 경쟁을 확실히 만든다
    both_read = threading.Barrier(2, timeout=5)
    original_get = repository.get_refresh_token

    def get_then_wait(db: Session, token_hash: str) -> RefreshToken | None:
        token = original_get(db, token_hash)
        both_read.wait()
        return token

    monkeypatch.setattr(repository, "get_refresh_token", get_then_wait)
    results: dict[str, Any] = {}

    def rotate(name: str) -> None:
        with Session(engine, expire_on_commit=False) as db:
            try:
                results[name] = service.rotate(db, refresh_token)
            except Exception as exc:
                results[name] = exc

    try:
        threads = [threading.Thread(target=rotate, args=(name,)) for name in ("a", "b")]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(10)

        issued = [v for v in results.values() if isinstance(v, service.IssuedSession)]
        assert len(issued) == 2, results

        with Session(engine) as check:
            alive = _alive(check, device_id)
        assert len(alive) == 1
        assert alive[0] in {security.hash_refresh_token(v.refresh_token) for v in issued}
    finally:
        with Session(engine) as cleanup:
            cleanup.delete(cleanup.get(User, user_id))  # 토큰은 CASCADE
            cleanup.commit()


# --- 회전 응답을 못 받은 재시도 --------------------------------------------


def test_just_rotated_token_is_rotated_again(
    db_session: Session, redis_client: redis.Redis, fake_google: dict[str, Any]
) -> None:
    """회전 응답을 받기 전에 새로고침한 브라우저가 옛 토큰을 다시 보낸다. 로그아웃시키지 않는다."""
    issued = login(db_session, redis_client)
    lost = service.rotate(db_session, issued.refresh_token)  # 브라우저가 버린 응답

    retried = service.rotate(db_session, issued.refresh_token)

    assert retried.device_id == issued.device_id
    assert retried.refresh_token != lost.refresh_token
    # 기기당 살아 있는 토큰은 여전히 하나다
    assert _alive(db_session, issued.device_id) == [
        security.hash_refresh_token(retried.refresh_token)
    ]
    assert service.rotate(db_session, retried.refresh_token).device_id == issued.device_id


def test_retry_can_repeat_within_the_grace_window(
    db_session: Session, redis_client: redis.Redis, fake_google: dict[str, Any]
) -> None:
    """새로고침을 연달아 해도 매번 이어 준다."""
    issued = login(db_session, redis_client)
    service.rotate(db_session, issued.refresh_token)
    service.rotate(db_session, issued.refresh_token)

    last = service.rotate(db_session, issued.refresh_token)

    assert _alive(db_session, issued.device_id) == [security.hash_refresh_token(last.refresh_token)]


def test_token_pushed_out_by_a_retry_counts_as_reuse(
    db_session: Session, redis_client: redis.Redis, fake_google: dict[str, Any]
) -> None:
    """재시도로 밀려난 토큰이 나중에 오면 그 응답을 누군가 받았다는 뜻이다. 탈취로 본다."""
    issued = login(db_session, redis_client)
    pushed_out = service.rotate(db_session, issued.refresh_token)
    service.rotate(db_session, issued.refresh_token)

    with pytest.raises(RefreshTokenReused):
        service.rotate(db_session, pushed_out.refresh_token)

    assert _alive(db_session, issued.device_id) == []


def test_thief_in_the_grace_window_is_caught_when_the_user_refreshes(
    db_session: Session, redis_client: redis.Redis, fake_google: dict[str, Any]
) -> None:
    """사용자가 회전한 직후 탈취한 쪽이 옛 토큰을 쓴다. 사용자가 다시 갱신할 때 둘 다 끊긴다."""
    stolen = login(db_session, redis_client)
    user = service.rotate(db_session, stolen.refresh_token)
    thief = service.rotate(db_session, stolen.refresh_token)

    with pytest.raises(RefreshTokenReused):
        service.rotate(db_session, user.refresh_token)

    assert _alive(db_session, stolen.device_id) == []
    with pytest.raises(UnauthorizedException):
        service.rotate(db_session, thief.refresh_token)


def test_thief_in_the_grace_window_keeps_the_session_if_the_user_never_returns(
    db_session: Session, redis_client: redis.Redis, fake_google: dict[str, Any]
) -> None:
    """한계: 밀려난 사용자가 다시 갱신하지 않으면 탈취한 쪽은 계속 갱신할 수 있다."""
    stolen = login(db_session, redis_client)
    service.rotate(db_session, stolen.refresh_token)
    thief = service.rotate(db_session, stolen.refresh_token)
    _past_grace(db_session, stolen.refresh_token)

    renewed = service.rotate(db_session, thief.refresh_token)

    assert _alive(db_session, stolen.device_id) == [
        security.hash_refresh_token(renewed.refresh_token)
    ]


def test_no_retry_once_the_new_token_was_used(
    db_session: Session, redis_client: redis.Redis, fake_google: dict[str, Any]
) -> None:
    """이어받은 토큰이 이미 쓰였으면 응답을 받은 쪽이 있다. 옛 토큰은 탈취본이다."""
    issued = login(db_session, redis_client)
    current = service.rotate(db_session, issued.refresh_token)
    service.rotate(db_session, current.refresh_token)

    with pytest.raises(RefreshTokenReused):
        service.rotate(db_session, issued.refresh_token)

    assert _alive(db_session, issued.device_id) == []


def test_no_retry_after_logout(
    db_session: Session, redis_client: redis.Redis, fake_google: dict[str, Any]
) -> None:
    issued = login(db_session, redis_client)
    current = service.rotate(db_session, issued.refresh_token)
    service.logout(db_session, current.refresh_token)

    with pytest.raises(UnauthorizedException):
        service.rotate(db_session, issued.refresh_token)

    assert _alive(db_session, issued.device_id) == []


def test_unknown_refresh_token_is_rejected(db_session: Session) -> None:
    with pytest.raises(InvalidRefreshToken):
        service.rotate(db_session, security.create_refresh_token())


def test_missing_refresh_token_is_rejected(db_session: Session) -> None:
    with pytest.raises(InvalidRefreshToken):
        service.rotate(db_session, None)

    with pytest.raises(InvalidRefreshToken):
        service.rotate(db_session, "")


def test_expired_refresh_token_is_rejected(
    db_session: Session, redis_client: redis.Redis, fake_google: dict[str, Any]
) -> None:
    issued = login(db_session, redis_client)
    stored = repository.get_refresh_token(
        db_session, security.hash_refresh_token(issued.refresh_token)
    )
    assert stored is not None
    stored.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    db_session.commit()

    with pytest.raises(InvalidRefreshToken):
        service.rotate(db_session, issued.refresh_token)

    db_session.refresh(stored)
    assert stored.revoked_at is not None


def test_deleting_a_user_kills_their_refresh_tokens(
    db_session: Session, redis_client: redis.Redis, fake_google: dict[str, Any]
) -> None:
    """탈퇴하면 남은 Refresh 로 다시 들어올 수 없다.

    FK 의 ON DELETE CASCADE 가 토큰 행을 지운다. 그래서 "사용자는 없는데 토큰은 살아 있는"
    상태 자체가 DB 에 존재할 수 없다 (service 의 user is None 분기는 그 뒤를 받치는 방어선).
    """
    issued = login(db_session, redis_client)

    db_session.delete(issued.user)
    db_session.commit()

    assert db_session.scalars(select(RefreshToken)).all() == []
    with pytest.raises(InvalidRefreshToken):
        service.rotate(db_session, issued.refresh_token)


# --- 로그아웃 -----------------------------------------------------------


def test_logout_revokes_the_session(
    db_session: Session, redis_client: redis.Redis, fake_google: dict[str, Any]
) -> None:
    issued = login(db_session, redis_client)

    service.logout(db_session, issued.refresh_token)

    # 폐기된 토큰이 다시 오면 재사용 탐지에 걸린다. 사용자에게는 같은 401 이다.
    with pytest.raises(UnauthorizedException):
        service.rotate(db_session, issued.refresh_token)


def test_logout_revokes_rotated_tokens_of_the_same_device(
    db_session: Session, redis_client: redis.Redis, fake_google: dict[str, Any]
) -> None:
    issued = login(db_session, redis_client)
    current = service.rotate(db_session, issued.refresh_token)

    service.logout(db_session, current.refresh_token)

    alive = db_session.scalars(select(RefreshToken).where(RefreshToken.revoked_at.is_(None))).all()
    assert alive == []


def test_logout_leaves_other_devices_alone(
    db_session: Session, redis_client: redis.Redis, fake_google: dict[str, Any]
) -> None:
    laptop = login(db_session, redis_client)
    phone = login(db_session, redis_client)

    service.logout(db_session, laptop.refresh_token)

    assert service.rotate(db_session, phone.refresh_token) is not None


def test_logout_is_idempotent_and_silent(
    db_session: Session, redis_client: redis.Redis, fake_google: dict[str, Any]
) -> None:
    """토큰 존재 여부를 알려주지 않는다. 두 번 호출해도 오류가 없다."""
    issued = login(db_session, redis_client)

    service.logout(db_session, issued.refresh_token)
    service.logout(db_session, issued.refresh_token)
    service.logout(db_session, None)
    service.logout(db_session, security.create_refresh_token())
