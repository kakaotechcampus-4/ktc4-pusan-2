"""state_store 는 진짜 PostgreSQL 을 상대로 테스트한다.

동시 소비는 실제 커넥션 두 개로 확인한다. 테스트 세션 하나 안에서는 DELETE ... RETURNING 이
한 요청만 통과시키는지 볼 수 없다.
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier

from sqlalchemy import Engine, delete, func, select, text, update
from sqlalchemy.orm import Session

from pitch_coach_backend.module.auth import state_store
from pitch_coach_backend.module.auth.entity import OAuthAuthorizationRequest
from pitch_coach_backend.module.auth.state_store import AuthorizationRequest


def _request(**kw: str) -> AuthorizationRequest:
    return AuthorizationRequest(
        nonce=kw.get("nonce", "nonce-1"),
        code_verifier=kw.get("code_verifier", "verifier-1"),
        browser_token=kw.get("browser_token", "browser-1"),
        return_to=kw.get("return_to", "/"),
    )


def _expire(db: Session, state: str) -> None:
    db.execute(
        update(OAuthAuthorizationRequest)
        .where(OAuthAuthorizationRequest.state == state)
        .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
    )


def _count(db: Session, state: str) -> int:
    return db.scalar(select(func.count()).where(OAuthAuthorizationRequest.state == state))


def test_generated_values_are_unique() -> None:
    assert len({state_store.new_state() for _ in range(100)}) == 100
    assert len({state_store.new_nonce() for _ in range(100)}) == 100
    assert len({state_store.new_browser_token() for _ in range(100)}) == 100


def test_save_then_consume_roundtrip(db_session: Session) -> None:
    state = state_store.new_state()
    original = _request(return_to="/pitches/1")
    state_store.save(db_session, state, original)

    assert state_store.consume(db_session, state) == original


def test_consume_is_one_shot(db_session: Session) -> None:
    state = state_store.new_state()
    state_store.save(db_session, state, _request())

    assert state_store.consume(db_session, state) is not None
    assert state_store.consume(db_session, state) is None


def test_unknown_state_returns_none(db_session: Session) -> None:
    assert state_store.consume(db_session, "never-issued") is None


def test_saved_request_expires_in_ten_minutes(db_session: Session) -> None:
    state = state_store.new_state()
    before = datetime.now(UTC)
    state_store.save(db_session, state, _request())

    stored = db_session.get(OAuthAuthorizationRequest, state)
    assert stored is not None
    left = stored.expires_at - before
    assert timedelta(seconds=599) <= left <= timedelta(seconds=601)


def test_expired_request_is_rejected_and_removed(db_session: Session) -> None:
    """정리가 아직 안 돌았어도 만료된 요청은 통과하지 못한다."""
    state = state_store.new_state()
    state_store.save(db_session, state, _request())
    _expire(db_session, state)

    assert state_store.consume(db_session, state) is None
    assert _count(db_session, state) == 0


def test_save_removes_expired_requests(db_session: Session) -> None:
    """콜백이 오지 않은 요청은 다음 로그인 시작 때 지운다. 살아 있는 요청은 남긴다."""
    abandoned, alive = state_store.new_state(), state_store.new_state()
    state_store.save(db_session, abandoned, _request())
    state_store.save(db_session, alive, _request())
    _expire(db_session, abandoned)

    state_store.save(db_session, state_store.new_state(), _request())

    assert _count(db_session, abandoned) == 0
    assert _count(db_session, alive) == 1


def test_states_do_not_collide(db_session: Session) -> None:
    first, second = state_store.new_state(), state_store.new_state()
    state_store.save(db_session, first, _request(nonce="first"))
    state_store.save(db_session, second, _request(nonce="second"))

    assert state_store.consume(db_session, first).nonce == "first"
    assert state_store.consume(db_session, second).nonce == "second"


def test_two_connections_cannot_consume_one_request(engine: Engine) -> None:
    """같은 state 로 콜백 두 개가 동시에 와도 하나만 요청을 받는다."""
    state = state_store.new_state()
    with Session(engine) as db:
        state_store.save(db, state, _request())
        db.commit()
    barrier = Barrier(2)

    def consume() -> AuthorizationRequest | None:
        with Session(engine) as db:
            barrier.wait(timeout=5)
            pending = state_store.consume(db, state)
            db.commit()
            return pending

    try:
        with ThreadPoolExecutor(max_workers=2) as workers:
            outcomes = list(workers.map(lambda _: consume(), range(2)))
        assert sorted(outcome is not None for outcome in outcomes) == [False, True]
    finally:
        with Session(engine) as db:
            db.execute(
                delete(OAuthAuthorizationRequest).where(OAuthAuthorizationRequest.state == state)
            )
            db.commit()


def test_cleanup_skips_rows_another_request_holds(engine: Engine) -> None:
    """만료된 행을 다른 요청이 잡고 있어도 로그인 시작은 기다리지 않는다."""
    held, started = state_store.new_state(), state_store.new_state()
    with Session(engine) as db:
        state_store.save(db, held, _request())
        _expire(db, held)
        db.commit()

    try:
        with Session(engine) as holder, Session(engine) as db:
            holder.execute(
                select(OAuthAuthorizationRequest)
                .where(OAuthAuthorizationRequest.state == held)
                .with_for_update()
            )
            # 잠긴 행을 기다리면 1초 뒤 오류가 난다
            db.execute(text("SET LOCAL lock_timeout = '1s'"))
            state_store.save(db, started, _request())
            db.commit()
            holder.rollback()
    finally:
        with Session(engine) as db:
            db.execute(
                delete(OAuthAuthorizationRequest).where(
                    OAuthAuthorizationRequest.state.in_([held, started])
                )
            )
            db.commit()
