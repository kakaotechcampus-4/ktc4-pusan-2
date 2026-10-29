"""발표 파일 삭제 기록: 발표를 지운 커밋에 남고, 지우기에 성공할 때까지 다시 한다."""

import uuid
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from pitch_coach_backend.core.security import create_access_token
from pitch_coach_backend.module.pitch import file_cleanup_sweep
from pitch_coach_backend.module.pitch.dto import PitchSaveRequestDTO
from pitch_coach_backend.module.pitch.entity import PendingFileDeletion
from pitch_coach_backend.module.pitch.service import file_cleanup
from pitch_coach_backend.module.pitch.service import pitch as pitch_service


@pytest.fixture
def session_factory(db_session: Session) -> Callable[[], Session]:
    """테스트 트랜잭션 안에서 짧은 세션을 여닫는다. 커밋은 savepoint 로 처리된다."""

    def factory() -> Session:
        return Session(
            bind=db_session.get_bind(),
            join_transaction_mode="create_savepoint",
            autoflush=False,
            expire_on_commit=False,
        )

    return factory


def _rows(db: Session) -> dict[str, int]:
    db.expire_all()
    return {row.prefix: row.attempts for row in db.scalars(select(PendingFileDeletion))}


def _add_pitch(db: Session, user_id: uuid.UUID) -> uuid.UUID:
    return pitch_service.add_pitch_service(
        db,
        user_id,
        PitchSaveRequestDTO(title="발표", time_limit_sec=300, presentation_date=date(2026, 3, 1)),
    )


def test_deleting_a_pitch_leaves_a_record_in_the_same_commit(
    client: TestClient, db_session: Session, user_id: uuid.UUID
) -> None:
    """백그라운드 삭제가 끝나기 전에 프로세스가 끊겨도 지울 경로가 남는다."""
    pitch_id = _add_pitch(db_session, user_id)

    res = client.delete(
        f"/api/pitches/delete/{pitch_id}",
        headers={"Authorization": f"Bearer {create_access_token(user_id)}"},
    )

    assert res.status_code == 200
    assert _rows(db_session) == {f"pitches/{pitch_id}/": 0}


def test_reserving_the_same_prefix_twice_keeps_one_record(db_session: Session) -> None:
    pitch_id = uuid.uuid4()

    file_cleanup.reserve(db_session, [pitch_id, pitch_id])
    file_cleanup.reserve(db_session, [pitch_id])

    assert list(_rows(db_session)) == [f"pitches/{pitch_id}/"]


def test_successful_delete_removes_the_record(
    db_session: Session,
    session_factory: Callable[[], Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pitch_id = uuid.uuid4()
    prefix = file_cleanup.pitch_prefix(pitch_id)
    file_cleanup.reserve(db_session, [pitch_id])
    deleted: list[str] = []
    monkeypatch.setattr(file_cleanup, "delete_prefix", deleted.append)

    assert file_cleanup.delete_files(prefix, session_factory) is True

    assert deleted == [prefix]
    assert _rows(db_session) == {}


def test_failed_delete_keeps_the_record_and_counts_the_failure(
    db_session: Session,
    session_factory: Callable[[], Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pitch_id = uuid.uuid4()
    prefix = file_cleanup.pitch_prefix(pitch_id)
    file_cleanup.reserve(db_session, [pitch_id])

    def fail(prefix: str) -> None:
        raise Exception("S3 파일 삭제 실패")

    monkeypatch.setattr(file_cleanup, "delete_prefix", fail)

    assert file_cleanup.delete_files(prefix, session_factory) is False
    assert file_cleanup.delete_files(prefix, session_factory) is False

    assert _rows(db_session) == {prefix: 2}


def test_retry_deletes_old_records_and_skips_fresh_ones(
    db_session: Session,
    session_factory: Callable[[], Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    old_id, fresh_id = uuid.uuid4(), uuid.uuid4()
    file_cleanup.reserve(db_session, [old_id, fresh_id])
    db_session.execute(
        update(PendingFileDeletion)
        .where(PendingFileDeletion.prefix == file_cleanup.pitch_prefix(old_id))
        .values(created_at=datetime.now(UTC) - timedelta(minutes=10))
    )
    deleted: list[str] = []
    monkeypatch.setattr(file_cleanup, "delete_prefix", deleted.append)

    assert file_cleanup.retry_pending(session_factory) == 1

    assert deleted == [file_cleanup.pitch_prefix(old_id)]
    assert list(_rows(db_session)) == [file_cleanup.pitch_prefix(fresh_id)]


@pytest.mark.anyio
async def test_sweep_retries_what_an_earlier_failure_left(
    db_session: Session,
    session_factory: Callable[[], Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """S3 가 한 번 실패한 뒤 주기 작업이 지운다."""
    pitch_id = uuid.uuid4()
    file_cleanup.reserve(db_session, [pitch_id])
    db_session.execute(
        update(PendingFileDeletion).values(created_at=datetime(2026, 1, 1, tzinfo=UTC))
    )
    calls: list[str] = []

    def flaky(prefix: str) -> None:
        calls.append(prefix)
        if len(calls) == 1:
            raise Exception("S3 파일 삭제 실패")

    monkeypatch.setattr(file_cleanup, "delete_prefix", flaky)

    assert await file_cleanup_sweep.sweep_once(session_factory) == 0
    assert _rows(db_session) == {file_cleanup.pitch_prefix(pitch_id): 1}
    assert await file_cleanup_sweep.sweep_once(session_factory) == 1

    assert _rows(db_session) == {}


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
