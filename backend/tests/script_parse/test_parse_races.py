"""대본 파싱 권한 · 조건부 UPDATE 경합 · 같은 pitch 의 동시 생성."""

import threading
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from pitch_coach_backend.core.security import create_access_token
from pitch_coach_backend.module.pitch.adapter.script_parser import (
    ScriptParseError,
)
from pitch_coach_backend.module.pitch.dto import ScriptParseErrorCode
from pitch_coach_backend.module.pitch.entity import Pitch, ScriptParseStatus, ScriptVersion
from pitch_coach_backend.module.pitch.repository import PitchRepository
from pitch_coach_backend.module.pitch.service import script as script_service
from tests.script_parse.conftest import FakeParser, _age, _create_ok, _get, _new_pitch, _reparse


@pytest.fixture
def other_user_headers(db_session: Session) -> dict[str, str]:
    from pitch_coach_backend.module.user.entity import User

    user = User(email=f"{uuid.uuid4()}@example.com", name="다른 사용자")
    db_session.add(user)
    db_session.flush()
    return {"Authorization": f"Bearer {create_access_token(user.id)}"}


# ── 권한 ───────────────────────────────────────────────────────────


def test_other_users_script_is_not_found(
    client: TestClient,
    auth_headers: dict[str, str],
    other_user_headers: dict[str, str],
    pitch_id: uuid.UUID,
) -> None:
    script_id = _create_ok(client, pitch_id, auth_headers)

    for response in (
        _get(client, pitch_id, script_id, other_user_headers),
        _reparse(client, pitch_id, script_id, other_user_headers),
    ):
        assert response.status_code == 404
        assert response.json()["code"] == "PITCH_NOT_FOUND"


def test_script_of_another_pitch_is_not_found(
    client: TestClient,
    auth_headers: dict[str, str],
    pitch_id: uuid.UUID,
) -> None:
    """내 pitch 두 개 사이에서 id 를 바꿔 끼워도 안 된다."""
    script_id = _create_ok(client, pitch_id, auth_headers)
    other_pitch = _new_pitch(client, auth_headers)

    for response in (
        _get(client, other_pitch, script_id, auth_headers),
        _reparse(client, other_pitch, script_id, auth_headers),
        _get(client, pitch_id, uuid.uuid7(), auth_headers),
    ):
        assert response.status_code == 404
        assert response.json()["code"] == "SCRIPT_NOT_FOUND"


# ── 조건부 UPDATE 에서 진 요청 ────────────────────────────────────────


# 순서대로 보내면 두 번째 요청은 service 의 빠른 검사에서 걸려 UPDATE 까지 가지 않는다.
# 그래서 "검사는 통과했는데 UPDATE 직전에 다른 쪽이 먼저 바꾼" 상황을 직접 만든다.


def test_restart_parse_lets_only_one_of_two_win(
    client: TestClient,
    db_session: Session,
    auth_headers: dict[str, str],
    pitch_id: uuid.UUID,
) -> None:
    script_id = _create_ok(client, pitch_id, auth_headers)
    _age(db_session, script_id, 3600)
    repo = PitchRepository(db_session)
    now = datetime.now(UTC)
    expired_before = now - script_service.PARSE_EXPIRE_AFTER

    assert repo.restart_parse(script_id, now, expired_before) is True
    assert repo.restart_parse(script_id, now, expired_before) is False


# monkeypatch 전에 잡아 둔 원래 함수. competitor 가 이걸 불러야 재귀하지 않는다
_REAL_RESTART_PARSE = PitchRepository.restart_parse


def _race(
    monkeypatch: pytest.MonkeyPatch,
    db: Session,
    competitor: Callable[[uuid.UUID], None],
) -> None:
    """restart_parse 직전에 다른 요청(competitor)이 먼저 커밋하게 만든다."""
    real = _REAL_RESTART_PARSE

    def racing(self: PitchRepository, script_id: uuid.UUID, now: Any, expired_before: Any):
        competitor(script_id)
        db.commit()
        return real(self, script_id, now, expired_before)

    monkeypatch.setattr(PitchRepository, "restart_parse", racing)


def test_losing_a_retry_race_answers_in_progress(
    client: TestClient,
    db_session: Session,
    parser: FakeParser,
    monkeypatch: pytest.MonkeyPatch,
    auth_headers: dict[str, str],
    pitch_id: uuid.UUID,
) -> None:
    parser.result = ScriptParseError(ScriptParseErrorCode.AI_ERROR, "503", retryable=True)
    script_id = _create_ok(client, pitch_id, auth_headers)
    parser.calls.clear()

    def other_retry(script_id: uuid.UUID) -> None:
        now = datetime.now(UTC)
        repo = PitchRepository(db_session)
        _REAL_RESTART_PARSE(repo, script_id, now, now - script_service.PARSE_EXPIRE_AFTER)

    _race(monkeypatch, db_session, other_retry)
    response = _reparse(client, pitch_id, script_id, auth_headers)

    assert response.status_code == 409
    assert response.json()["code"] == "SCRIPT_PARSE_IN_PROGRESS"
    # 진 쪽은 작업을 띄우지 않는다
    assert parser.calls == []


def test_losing_to_a_finished_job_answers_already_parsed(
    client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    auth_headers: dict[str, str],
    pitch_id: uuid.UUID,
) -> None:
    """검사와 UPDATE 사이에 살아 있던 작업이 DONE 을 쓴 경우.

    IN_PROGRESS 가 아니라 ALREADY_PARSED 로 답해야 FE 가 폴링으로 결과를 가져간다.
    """
    script_id = _create_ok(client, pitch_id, auth_headers)
    _age(db_session, script_id, 3600)

    def job_finished(script_id: uuid.UUID) -> None:
        script = db_session.get(ScriptVersion, script_id)
        assert script is not None
        script.parse_status = ScriptParseStatus.DONE

    _race(monkeypatch, db_session, job_finished)
    response = _reparse(client, pitch_id, script_id, auth_headers)

    assert response.status_code == 409
    assert response.json()["code"] == "SCRIPT_ALREADY_PARSED"


# ── 같은 pitch 에 동시에 올리기 ────────────────────────────────────────────────


def test_concurrent_script_creates_on_one_pitch_get_distinct_versions(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """같은 pitch 에 대본 둘이 동시에 와도 둘 다 성공하고 버전이 1, 2 로 갈린다.

    잠금이 없으면 둘 다 max(version)+1 = 1 을 받아 두 번째가 unique 위반(500)이 된다.
    트랜잭션 두 개가 실제로 겹쳐야 해서 테스트 세션(savepoint) 대신 진짜 세션 둘을 쓰고,
    만든 데이터는 끝에 지운다.
    """
    from pitch_coach_backend.module.user.entity import User

    first_request_numbering = threading.Event()
    real_next_version = PitchRepository.next_script_version

    def slow_next_version(self: PitchRepository, pitch_id: uuid.UUID) -> int:
        # 첫 요청이 번호를 매기는 중(잠금을 쥔 채)에 두 번째 요청이 들어오게 한다
        version = real_next_version(self, pitch_id)
        if not first_request_numbering.is_set():
            first_request_numbering.set()
            time.sleep(0.3)
        return version

    monkeypatch.setattr(PitchRepository, "next_script_version", slow_next_version)

    with Session(engine) as setup:
        user = User(email=f"{uuid.uuid4()}@example.com", name="동시 업로드")
        setup.add(user)
        setup.flush()
        pitch = Pitch(user_id=user.id, title="동시", time_limit_sec=60)
        setup.add(pitch)
        setup.commit()
        user_id, target = user.id, pitch.id

    results: dict[str, Any] = {}

    def create(name: str) -> None:
        with Session(engine, expire_on_commit=False) as db:
            try:
                created, _ = script_service.create_script_service(db, target, f"대본 {name}")
                results[name] = created.version
            except Exception as exc:  # 잠금이 없으면 여기로 온다
                results[name] = exc

    try:
        first = threading.Thread(target=create, args=("a",))
        first.start()
        assert first_request_numbering.wait(5)
        second = threading.Thread(target=create, args=("b",))
        second.start()
        first.join(10)
        second.join(10)

        # 잠금이 없으면 한쪽이 IntegrityError(unique 위반) 다
        assert all(isinstance(v, int) for v in results.values()), results
        assert sorted(results.values()) == [1, 2]
    finally:
        with Session(engine) as cleanup:
            cleanup.delete(cleanup.get(User, user_id))  # pitch·버전은 CASCADE
            cleanup.commit()


def test_version_lock_does_not_block_child_inserts_on_the_same_pitch(engine: Engine) -> None:
    """버전 잠금(lock_for_new_version)을 쥔 동안에도 같은 pitch 의 자식 행은 넣을 수 있다.

    자식 행 INSERT 는 FK 검사로 pitch 행에 KEY SHARE 를 건다. 잠금이 FOR UPDATE 면 그것과 충돌해
    커밋까지 멈추고, FOR NO KEY UPDATE 면 지나간다. 멈추면 lock_timeout 에 걸려 실패한다.
    Take 는 서비스 없이 직접 넣는다 — create_take_service 는 이 잠금을 직접 잡아 기다린다.
    """
    from sqlalchemy import text

    from pitch_coach_backend.module.pitch.entity import PresentationVersion
    from pitch_coach_backend.module.take.entity import Take
    from pitch_coach_backend.module.user.entity import User

    with Session(engine) as setup:
        user = User(email=f"{uuid.uuid4()}@example.com", name="잠금")
        setup.add(user)
        setup.flush()
        pitch = Pitch(user_id=user.id, title="잠금", time_limit_sec=60)
        setup.add(pitch)
        setup.flush()
        presentation = PresentationVersion(pitch_id=pitch.id, version=1, file_key="k")
        script = ScriptVersion(
            pitch_id=pitch.id, version=1, content="대본", parse_status=ScriptParseStatus.DONE
        )
        setup.add_all([presentation, script])
        setup.commit()
        user_id, pitch_id = user.id, pitch.id
        presentation_id, script_id = presentation.id, script.id

    try:
        with Session(engine) as locker, Session(engine) as take_writer:
            PitchRepository(locker).lock_for_new_version(pitch_id)  # 커밋하지 않고 쥐고 있는다

            take_writer.execute(text("SET LOCAL lock_timeout = '2s'"))
            take_writer.add(
                Take(
                    pitch_id=pitch_id,
                    take_number=1,
                    presentation_version_id=presentation_id,
                    script_version_id=script_id,
                    mode="COACHING",
                    script_mode="HIGHLIGHT",
                    goal_time_sec=60,
                )
            )
            take_writer.commit()  # FOR UPDATE 였다면 여기서 LockNotAvailable
            locker.rollback()
    finally:
        with Session(engine) as cleanup:
            cleanup.delete(cleanup.get(User, user_id))  # pitch·버전·take 는 CASCADE
            cleanup.commit()
