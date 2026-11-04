"""auth service: 동시 회전 · 회전 응답을 못 받은 재시도의 grace 창."""

import threading
import time
import uuid
from typing import Any

import pytest
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from pitch_coach_backend.core import security
from pitch_coach_backend.core.exceptions import UnauthorizedException
from pitch_coach_backend.module.auth import repository, service
from pitch_coach_backend.module.auth.entity import RefreshToken
from pitch_coach_backend.module.auth.exception import (
    RefreshTokenReused,
)
from pitch_coach_backend.module.user.entity import User
from tests.auth.conftest import _past_grace, login


def _alive(db: Session, device_id: uuid.UUID) -> list[str]:
    """이 기기에서 살아 있는 토큰의 해시."""
    return list(
        db.scalars(
            select(RefreshToken.token_hash).where(
                RefreshToken.device_id == device_id, RefreshToken.revoked_at.is_(None)
            )
        )
    )


# --- 회전 ---------------------------------------------------------------


def _committed_token(engine: Engine) -> tuple[str, uuid.UUID, uuid.UUID]:
    """동시성 테스트용. 트랜잭션이 실제로 겹쳐야 해서 테스트 세션(savepoint) 대신 커밋한다."""
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
            session_expires_at=security.refresh_session_expires_at(),
        )
        setup.commit()
        return refresh_token, device_id, user.id


def _rotate_together(
    engine: Engine, monkeypatch: pytest.MonkeyPatch, refresh_token: str, count: int
) -> list[service.IssuedSession | Exception]:
    """같은 토큰으로 count 개를 함께 출발시킨다. 회전마다 커밋을 늦춰 요청이 서로 겹치게 한다."""
    start = threading.Barrier(count, timeout=5)
    original_create = security.create_refresh_token

    def create_slowly() -> str:
        time.sleep(0.3)
        return original_create()

    monkeypatch.setattr(security, "create_refresh_token", create_slowly)
    results: list[service.IssuedSession | Exception] = []

    def rotate() -> None:
        with Session(engine, expire_on_commit=False) as db:
            start.wait()
            try:
                results.append(service.rotate(db, refresh_token))
            except Exception as exc:
                results.append(exc)

    threads = [threading.Thread(target=rotate) for _ in range(count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)
    monkeypatch.undo()
    return results


def _delete_user(engine: Engine, user_id: uuid.UUID) -> None:
    with Session(engine) as cleanup:
        cleanup.delete(cleanup.get(User, user_id))  # 토큰은 CASCADE
        cleanup.commit()


# --- 동시 회전 ----------------------------------------------------------


def test_concurrent_rotation_keeps_one_live_token(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """같은 Refresh 로 요청 둘이 동시에 온다 (회전 중에 새로고침했거나 탈취한 쪽과 겹친 경우).

    둘 다 revoked_at IS NULL 을 보고 각자 새 토큰을 받으면 계보가 갈라진다.
    늦은 쪽은 먼저 회전한 쪽의 새 토큰을 폐기하고 이어받아, 살아 있는 토큰은 하나만 남는다.
    """
    refresh_token, device_id, user_id = _committed_token(engine)
    try:
        results = _rotate_together(engine, monkeypatch, refresh_token, 2)

        issued = [r for r in results if isinstance(r, service.IssuedSession)]
        assert len(issued) == 2, results
        with Session(engine) as check:
            alive = _alive(check, device_id)
        assert len(alive) == 1
        assert alive[0] in {security.hash_refresh_token(v.refresh_token) for v in issued}
    finally:
        _delete_user(engine, user_id)


def test_retries_arriving_together_after_a_rotation_all_succeed(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """한 요청이 회전을 마친 뒤 같은 옛 토큰으로 요청 둘이 함께 온다 (같은 토큰으로 셋째 요청).

    잠그지 않으면 둘 다 "첫 회전의 새 토큰" 을 이어받으려 한다. 한쪽이 먼저 폐기하면 다른 쪽은
    재사용으로 걸려 그 브라우저가 로그아웃된다. 옛 토큰 행을 잠가 차례로 이어받게 한다.
    """
    refresh_token, device_id, user_id = _committed_token(engine)
    try:
        with Session(engine, expire_on_commit=False) as first:
            service.rotate(first, refresh_token)

        results = _rotate_together(engine, monkeypatch, refresh_token, 2)

        issued = [r for r in results if isinstance(r, service.IssuedSession)]
        assert len(issued) == 2, results
        with Session(engine) as check:
            alive = _alive(check, device_id)
        assert len(alive) == 1
        assert alive[0] in {security.hash_refresh_token(v.refresh_token) for v in issued}
    finally:
        _delete_user(engine, user_id)


# --- 회전 응답을 못 받은 재시도 --------------------------------------------


def test_just_rotated_token_is_rotated_again(
    db_session: Session, fake_google: dict[str, Any]
) -> None:
    """회전 응답을 받기 전에 새로고침한 브라우저가 옛 토큰을 다시 보낸다. 로그아웃시키지 않는다."""
    issued = login(db_session)
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
    db_session: Session, fake_google: dict[str, Any]
) -> None:
    """새로고침을 연달아 해도 매번 이어 준다."""
    issued = login(db_session)
    service.rotate(db_session, issued.refresh_token)
    service.rotate(db_session, issued.refresh_token)

    last = service.rotate(db_session, issued.refresh_token)

    assert _alive(db_session, issued.device_id) == [security.hash_refresh_token(last.refresh_token)]


def test_token_pushed_out_by_a_retry_counts_as_reuse(
    db_session: Session, fake_google: dict[str, Any]
) -> None:
    """재시도로 밀려난 토큰이 나중에 오면 그 응답을 누군가 받았다는 뜻이다. 탈취로 본다."""
    issued = login(db_session)
    pushed_out = service.rotate(db_session, issued.refresh_token)
    service.rotate(db_session, issued.refresh_token)

    with pytest.raises(RefreshTokenReused):
        service.rotate(db_session, pushed_out.refresh_token)

    assert _alive(db_session, issued.device_id) == []


def test_thief_in_the_grace_window_is_caught_when_the_user_refreshes(
    db_session: Session, fake_google: dict[str, Any]
) -> None:
    """사용자가 회전한 직후 탈취한 쪽이 옛 토큰을 쓴다. 사용자가 다시 갱신할 때 둘 다 끊긴다."""
    stolen = login(db_session)
    user = service.rotate(db_session, stolen.refresh_token)
    thief = service.rotate(db_session, stolen.refresh_token)

    with pytest.raises(RefreshTokenReused):
        service.rotate(db_session, user.refresh_token)

    assert _alive(db_session, stolen.device_id) == []
    with pytest.raises(UnauthorizedException):
        service.rotate(db_session, thief.refresh_token)


def test_thief_in_the_grace_window_keeps_the_session_if_the_user_never_returns(
    db_session: Session, fake_google: dict[str, Any]
) -> None:
    """한계: 밀려난 사용자가 다시 갱신하지 않으면 탈취한 쪽은 계속 갱신할 수 있다."""
    stolen = login(db_session)
    service.rotate(db_session, stolen.refresh_token)
    thief = service.rotate(db_session, stolen.refresh_token)
    _past_grace(db_session, stolen.refresh_token)

    renewed = service.rotate(db_session, thief.refresh_token)

    assert _alive(db_session, stolen.device_id) == [
        security.hash_refresh_token(renewed.refresh_token)
    ]


def test_no_retry_once_the_new_token_was_used(
    db_session: Session, fake_google: dict[str, Any]
) -> None:
    """이어받은 토큰이 이미 쓰였으면 응답을 받은 쪽이 있다. 옛 토큰은 탈취본이다."""
    issued = login(db_session)
    current = service.rotate(db_session, issued.refresh_token)
    service.rotate(db_session, current.refresh_token)

    with pytest.raises(RefreshTokenReused):
        service.rotate(db_session, issued.refresh_token)

    assert _alive(db_session, issued.device_id) == []


def test_no_retry_after_logout(db_session: Session, fake_google: dict[str, Any]) -> None:
    issued = login(db_session)
    current = service.rotate(db_session, issued.refresh_token)
    service.logout(db_session, current.refresh_token)

    with pytest.raises(UnauthorizedException):
        service.rotate(db_session, issued.refresh_token)

    assert _alive(db_session, issued.device_id) == []
