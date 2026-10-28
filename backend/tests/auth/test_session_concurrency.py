"""독립 PostgreSQL 연결로 회전·로그아웃·재사용 탐지의 실행 순서를 고정한다."""

import threading
import time
from collections.abc import Generator
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import pytest
from sqlalchemy import Engine, select, text
from sqlalchemy.orm import Session

from pitch_coach_backend.core.exceptions import UnauthorizedException
from pitch_coach_backend.module.auth import repository, service
from pitch_coach_backend.module.auth.entity import RefreshToken
from pitch_coach_backend.module.auth.exception import RefreshTokenReused
from pitch_coach_backend.module.user.entity import User
from tests.auth.conftest import _past_grace, login


@pytest.fixture
def committed_login(
    engine: Engine, fake_google: dict[str, Any]
) -> Generator[service.IssuedSession]:
    # savepoint 대신 실제로 커밋해 다른 연결에서도 보이게 한다.
    with Session(engine, expire_on_commit=False) as db:
        issued = login(db)
    try:
        yield issued
    finally:
        with Session(engine) as db:
            db.delete(db.get(User, issued.user.id))
            db.commit()


def _wait_for_lock(engine: Engine, pid: int) -> None:
    """잠금 대기를 확인한 뒤 앞선 요청을 풀어 줘, 스케줄러 운에 기대지 않는다."""
    deadline = time.monotonic() + 5
    with engine.connect() as conn:
        while time.monotonic() < deadline:
            waiting = conn.scalar(
                text("SELECT wait_event_type = 'Lock' FROM pg_stat_activity WHERE pid = :pid"),
                {"pid": pid},
            )
            conn.commit()  # 다음 조회에서 최신 활동 상태를 읽는다
            if waiting:
                return
            time.sleep(0.01)
    pytest.fail("두 번째 요청이 DB 잠금을 기다리지 않았습니다.")


def _alive(engine: Engine, issued: service.IssuedSession) -> int:
    with Session(engine) as db:
        return len(
            db.scalars(
                select(RefreshToken.id).where(
                    RefreshToken.device_id == issued.device_id,
                    RefreshToken.revoked_at.is_(None),
                )
            ).all()
        )


@pytest.mark.parametrize(
    ("rotation", "ending"),
    [
        ("first", "logout"),
        ("retry", "logout"),
        ("latest", "logout"),
        ("latest", "reuse"),
        ("rollback", "logout"),
    ],
)
def test_session_end_waits_for_rotation_and_revokes_its_new_token(
    engine: Engine,
    committed_login: service.IssuedSession,
    monkeypatch: pytest.MonkeyPatch,
    rotation: str,
    ending: str,
) -> None:
    """발급 중 로그아웃·재사용 탐지가 오면 커밋을 기다렸다가 새 토큰까지 폐기한다.

    retry는 옛 토큰의 유예 재회전, latest는 서로 다른 세대의 토큰을 쓴 경합이다.
    rollback은 발급 실패 뒤 트랜잭션 잠금도 풀리는지 확인한다.
    """
    original = committed_login.refresh_token
    rotating = original
    if rotation in ("retry", "latest"):
        with Session(engine, expire_on_commit=False) as db:
            current = service.rotate(db, original)
            if rotation == "latest":
                rotating = current.refresh_token
            if ending == "reuse":
                _past_grace(db, original)
                db.commit()

    minted = threading.Event()
    release = threading.Event()
    entered = threading.Event()
    end_pid: list[int] = []
    original_issue = service._issue_session

    def pause_before_commit(*args: Any, **kwargs: Any) -> service.IssuedSession:
        issued = original_issue(*args, **kwargs)
        minted.set()
        assert release.wait(10)
        if rotation == "rollback":
            raise RuntimeError("테스트용 발급 실패")
        return issued

    monkeypatch.setattr(service, "_issue_session", pause_before_commit)

    def rotate() -> service.IssuedSession | None:
        with Session(engine, expire_on_commit=False) as db:
            if rotation == "rollback":
                with pytest.raises(RuntimeError, match="테스트용 발급 실패"):
                    service.rotate(db, rotating)
                return None  # 세션을 닫으며 롤백한다
            return service.rotate(db, rotating)

    def end_session() -> None:
        with Session(engine) as db:
            end_pid.append(db.scalar(text("SELECT pg_backend_pid()")))
            entered.set()
            if ending == "reuse":
                with pytest.raises(RefreshTokenReused):
                    service.rotate(db, original)
            else:
                service.logout(db, original)

    with ThreadPoolExecutor(max_workers=2) as pool:
        issuing = pool.submit(rotate)
        try:
            assert minted.wait(5)
            terminating = pool.submit(end_session)
            assert entered.wait(5)
            _wait_for_lock(engine, end_pid[0])
            assert not terminating.done()
        finally:
            release.set()
        issued = issuing.result(timeout=5)
        terminating.result(timeout=5)

    assert _alive(engine, committed_login) == 0
    if issued is not None:
        with Session(engine) as db, pytest.raises(UnauthorizedException):
            service.rotate(db, issued.refresh_token)


def test_rotation_waits_for_logout_and_cannot_issue_a_new_token(
    engine: Engine,
    committed_login: service.IssuedSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """옛 토큰으로 로그아웃하는 동안 최신 토큰의 회전도 같은 잠금을 기다린다."""
    with Session(engine, expire_on_commit=False) as db:
        current = service.rotate(db, committed_login.refresh_token)
    revoked = threading.Event()
    release = threading.Event()
    entered = threading.Event()
    rotate_pid: list[int] = []
    original_revoke = repository.revoke_device

    def pause_before_commit(*args: Any, **kwargs: Any) -> int:
        count = original_revoke(*args, **kwargs)
        revoked.set()
        assert release.wait(10)
        return count

    monkeypatch.setattr(repository, "revoke_device", pause_before_commit)

    def logout() -> None:
        with Session(engine) as db:
            service.logout(db, committed_login.refresh_token)

    def rotate() -> None:
        with Session(engine) as db:
            rotate_pid.append(db.scalar(text("SELECT pg_backend_pid()")))
            entered.set()
            with pytest.raises(UnauthorizedException):
                service.rotate(db, current.refresh_token)

    with ThreadPoolExecutor(max_workers=2) as pool:
        terminating = pool.submit(logout)
        try:
            assert revoked.wait(5)
            issuing = pool.submit(rotate)
            assert entered.wait(5)
            _wait_for_lock(engine, rotate_pid[0])
            assert not issuing.done()
        finally:
            release.set()
        terminating.result(timeout=5)
        issuing.result(timeout=5)
    assert _alive(engine, committed_login) == 0


def test_rotating_one_device_does_not_block_another_device_of_the_same_user(
    engine: Engine,
    committed_login: service.IssuedSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with Session(engine, expire_on_commit=False) as db:
        other = login(db)
    assert other.user.id == committed_login.user.id
    assert other.device_id != committed_login.device_id
    minted = threading.Event()
    release = threading.Event()
    original_issue = service._issue_session

    def pause_before_commit(*args: Any, **kwargs: Any) -> service.IssuedSession:
        issued = original_issue(*args, **kwargs)
        minted.set()
        assert release.wait(10)
        return issued

    monkeypatch.setattr(service, "_issue_session", pause_before_commit)

    def rotate() -> service.IssuedSession:
        with Session(engine, expire_on_commit=False) as db:
            return service.rotate(db, committed_login.refresh_token)

    def logout_other_device() -> None:
        with Session(engine) as db:
            service.logout(db, other.refresh_token)

    with ThreadPoolExecutor(max_workers=2) as pool:
        issuing = pool.submit(rotate)
        try:
            assert minted.wait(5)
            pool.submit(logout_other_device).result(timeout=5)
            assert not issuing.done()
        finally:
            release.set()
        issuing.result(timeout=5)
    assert _alive(engine, committed_login) == 1
    assert _alive(engine, other) == 0
