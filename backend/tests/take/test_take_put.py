"""Take API — 부분 수정(PUT) · 끝내기 PUT."""

import uuid
from datetime import UTC, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect
from sqlalchemy.orm import Session

from pitch_coach_backend.module.take.entity import Take
from pitch_coach_backend.module.take.repository import TakeRepository
from tests.take.conftest import Versions, _make_take

# ── 가장 최근 Take ─────────────────────────────────────────────────


def test_latest_take_lookup_leaves_event_logs_unloaded(
    db_session: Session, pitch_id: uuid.UUID, versions: Versions
) -> None:
    """장치 점검은 번호만 본다. 시선 1초 기록이 쌓인 event_logs 는 읽지 않는다."""
    take = _make_take(db_session, pitch_id, versions, 1)
    take.event_logs = [{"type": "gaze"}]
    db_session.flush()
    db_session.expire_all()

    latest = TakeRepository(db_session).get_latest_take_in_pitch(pitch_id)

    assert latest is not None
    assert "event_logs" in inspect(latest).unloaded


# ── PUT /takes/{take_id} ────────────────────────────────────────────

# 시각은 Take 를 만든 때 근처여야 받는다. 고정 날짜로 두면 날이 지나 거절된다
_NOW = datetime.now(UTC).replace(microsecond=0)
_KST = timezone(timedelta(hours=9))


def _at(minutes: float) -> str:
    """지금부터 `minutes` 분 뒤 (한국 시간 표기)."""
    return (_NOW + timedelta(minutes=minutes)).astimezone(_KST).isoformat()


def _put(client: TestClient, take: Take, headers: dict[str, str], body: dict):
    return client.put(f"/api/pitches/{take.pitch_id}/takes/{take.id}", json=body, headers=headers)


def test_put_keeps_fields_that_were_not_sent(
    client: TestClient,
    db_session: Session,
    auth_headers: dict[str, str],
    pitch_id: uuid.UUID,
    versions: Versions,
) -> None:
    take = _make_take(db_session, pitch_id, versions, 1, status="RUNNING")
    started = _at(0)
    ended = _at(5)
    logs = [{"type": "slide", "at_ms": 0}]

    assert _put(client, take, auth_headers, {"started_at": started, "event_logs": logs}).is_success
    assert _put(client, take, auth_headers, {"ended_at": ended}).is_success

    db_session.refresh(take)
    assert take.started_at == datetime.fromisoformat(started)
    assert take.ended_at == datetime.fromisoformat(ended)
    assert take.event_logs == logs


def test_put_with_bad_datetime_is_422_not_500(
    client: TestClient,
    db_session: Session,
    auth_headers: dict[str, str],
    pitch_id: uuid.UUID,
    versions: Versions,
) -> None:
    take = _make_take(db_session, pitch_id, versions, 1)

    response = _put(client, take, auth_headers, {"started_at": "어제 저녁"})

    assert response.status_code == 422


def test_put_accepts_utc_z_suffix(
    client: TestClient,
    db_session: Session,
    auth_headers: dict[str, str],
    pitch_id: uuid.UUID,
    versions: Versions,
) -> None:
    take = _make_take(db_session, pitch_id, versions, 1, status="RUNNING")

    ended = _NOW.strftime("%Y-%m-%dT%H:%M:%SZ")

    assert _put(client, take, auth_headers, {"ended_at": ended}).is_success

    db_session.refresh(take)
    assert take.ended_at == _NOW


def test_put_with_naive_datetime_is_422(
    client: TestClient,
    db_session: Session,
    auth_headers: dict[str, str],
    pitch_id: uuid.UUID,
    versions: Versions,
) -> None:
    """시간대가 없으면 저장된 시각과 앞뒤를 비교할 수 없다."""
    take = _make_take(db_session, pitch_id, versions, 1)

    response = _put(client, take, auth_headers, {"started_at": "2026-10-07T10:00:00"})

    assert response.status_code == 422


def test_put_rejects_end_before_start(
    client: TestClient,
    db_session: Session,
    auth_headers: dict[str, str],
    pitch_id: uuid.UUID,
    versions: Versions,
) -> None:
    """한 요청 안에서도, 저장된 시작 시각과 비교해도 막는다."""
    take = _make_take(db_session, pitch_id, versions, 1, status="RUNNING")
    started = _at(0)
    before_start = _at(-1)

    together = _put(client, take, auth_headers, {"started_at": started, "ended_at": before_start})
    assert _put(client, take, auth_headers, {"started_at": started}).is_success
    separately = _put(client, take, auth_headers, {"ended_at": before_start})

    for response in (together, separately):
        assert response.status_code == 400
        assert response.json()["code"] == "INVALID_TAKE_TIME"
    db_session.refresh(take)
    assert take.ended_at is None


@pytest.mark.parametrize(
    "value",
    [
        "0001-01-01T00:00:00+05:00",
        "9999-12-31T23:59:59-05:00",
        (_NOW - timedelta(days=2)).isoformat(),
        (_NOW + timedelta(days=2)).isoformat(),
    ],
)
@pytest.mark.parametrize("field", ["started_at", "ended_at"])
def test_put_rejects_time_far_from_when_the_take_was_made(
    client: TestClient,
    db_session: Session,
    auth_headers: dict[str, str],
    pitch_id: uuid.UUID,
    versions: Versions,
    field: str,
    value: str,
) -> None:
    """0001년을 UTC 로 바꾸면 날짜 범위를 벗어나 500 이 되고 관리자 연습 시간도 망가진다."""
    take = _make_take(db_session, pitch_id, versions, 1, status="RUNNING")

    response = _put(client, take, auth_headers, {field: value})

    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION_ERROR"
    db_session.refresh(take)
    assert (take.started_at, take.ended_at) == (None, None)


def test_put_accepts_time_from_a_clock_hours_off(
    client: TestClient,
    db_session: Session,
    auth_headers: dict[str, str],
    pitch_id: uuid.UUID,
    versions: Versions,
) -> None:
    """시각은 FE 시계로 잰다. 시계가 몇 시간 빨라도 끝내기를 받는다 — 거절하면 FE 가 같은 값으로
    계속 다시 보내 Take 를 끝내지 못한다."""
    take = _make_take(db_session, pitch_id, versions, 1, status="RUNNING")
    started, ended = _at(3 * 60), _at(3 * 60 + 5)

    response = _put(client, take, auth_headers, {"started_at": started, "ended_at": ended})

    assert response.is_success
    db_session.refresh(take)
    assert take.ended_at == datetime.fromisoformat(ended)


def test_put_null_does_not_erase_a_field(
    client: TestClient,
    db_session: Session,
    auth_headers: dict[str, str],
    pitch_id: uuid.UUID,
    versions: Versions,
) -> None:
    take = _make_take(db_session, pitch_id, versions, 1)
    started = _at(0)

    assert _put(client, take, auth_headers, {"started_at": started}).is_success
    assert _put(client, take, auth_headers, {"started_at": None}).is_success

    db_session.refresh(take)
    assert take.started_at == datetime.fromisoformat(started)


@pytest.mark.parametrize("status", ["COMPLETED", "FAILED"])
def test_put_on_finished_take_is_409(
    client: TestClient,
    db_session: Session,
    auth_headers: dict[str, str],
    pitch_id: uuid.UUID,
    versions: Versions,
    status: str,
) -> None:
    take = _make_take(db_session, pitch_id, versions, 1)
    take.status = status
    db_session.flush()

    response = _put(client, take, auth_headers, {"ended_at": _at(5)})

    assert response.status_code == 409
    assert response.json()["code"] == "TAKE_ENDED"


def test_put_on_analyzing_take_takes_times_only(
    client: TestClient,
    db_session: Session,
    auth_headers: dict[str, str],
    pitch_id: uuid.UUID,
    versions: Versions,
) -> None:
    """FE 는 WS stop 으로 Take 를 끝낸 뒤 시각을 보낸다. 기록은 분석이 읽고 있어 받지 않는다."""
    take = _make_take(db_session, pitch_id, versions, 1)
    take.status = "ANALYZING"
    db_session.flush()
    started = _at(0)
    ended = _at(5)

    logs = _put(client, take, auth_headers, {"event_logs": [{"type": "slide", "at_ms": 0}]})
    times = _put(client, take, auth_headers, {"started_at": started, "ended_at": ended})

    assert logs.status_code == 409
    assert times.is_success
    db_session.refresh(take)
    assert (take.status, take.ended_at, take.event_logs) == (
        "ANALYZING",
        datetime.fromisoformat(ended),
        None,
    )


def test_put_end_finishes_a_take_that_never_sent_stop(
    client: TestClient,
    db_session: Session,
    auth_headers: dict[str, str],
    pitch_id: uuid.UUID,
    versions: Versions,
) -> None:
    """발표 중에 재연결을 포기한 FE 는 WS stop 을 못 보낸다. 시각을 보내는 PUT 이 Take 를 끝내야
    리포트 분석이 시작된다."""
    take = _make_take(db_session, pitch_id, versions, 1, status="RUNNING")
    ended = _at(5)

    response = _put(client, take, auth_headers, {"started_at": _at(0), "ended_at": ended})

    assert response.is_success
    db_session.refresh(take)
    assert (take.status, take.ended_at) == ("ANALYZING", datetime.fromisoformat(ended))


def test_put_end_rejects_a_take_that_never_started(
    client: TestClient,
    db_session: Session,
    auth_headers: dict[str, str],
    pitch_id: uuid.UUID,
    versions: Versions,
) -> None:
    """실시간 연결이 한 번도 붙지 않은(READY) Take 는 끝내지 않는다. FE 는 첫 ready 를 받은 뒤에
    발표를 시작하므로 남은 기록이 없다. 시각도 적지 않는다."""
    take = _make_take(db_session, pitch_id, versions, 1)

    response = _put(
        client,
        take,
        auth_headers,
        {"started_at": _at(0), "ended_at": _at(5)},
    )

    assert response.status_code == 409
    assert response.json()["code"] == "TAKE_NOT_STARTED"
    db_session.refresh(take)
    assert (take.status, take.started_at, take.ended_at) == ("READY", None, None)


def test_put_end_revives_a_take_ended_as_abandoned(
    client: TestClient,
    db_session: Session,
    auth_headers: dict[str, str],
    pitch_id: uuid.UUID,
    versions: Versions,
) -> None:
    """재연결을 포기한 FE 는 STT 없이 발표를 이어 간다. 그 사이 BE 가 떠난 것으로 끝냈어도
    끝내기 PUT 의 기록을 받아 분석으로 넘긴다. 끝낸 시각은 FE 값으로 덮는다."""
    take = _make_take(db_session, pitch_id, versions, 1)
    take.status = "FAILED"
    take.end_reason = "ABANDONED"
    take.ended_at = _NOW + timedelta(minutes=1)
    db_session.flush()
    ended = _at(50)
    logs = [{"type": "slide", "at_ms": 0}]

    early = _put(client, take, auth_headers, {"event_logs": logs})
    response = _put(
        client,
        take,
        auth_headers,
        {"started_at": _at(0), "ended_at": ended, "event_logs": logs},
    )

    assert early.status_code == 409, "끝내기(ended_at)가 아니면 받지 않는다"
    assert response.is_success
    db_session.refresh(take)
    assert (take.status, take.end_reason, take.ended_at, take.event_logs) == (
        "ANALYZING",
        None,
        datetime.fromisoformat(ended),
        logs,
    )


def test_put_end_does_not_revive_a_take_closed_with_its_tab(
    client: TestClient,
    db_session: Session,
    auth_headers: dict[str, str],
    pitch_id: uuid.UUID,
    versions: Versions,
) -> None:
    """창을 닫아 끝낸 Take(CLOSED)는 이어 가지 않는다. 닫은 탭을 복원해 끝내기를 눌러도 409 다."""
    take = _make_take(db_session, pitch_id, versions, 1)
    take.status = "FAILED"
    take.end_reason = "CLOSED"
    db_session.flush()

    response = _put(client, take, auth_headers, {"ended_at": _at(50)})

    assert response.status_code == 409
    assert response.json()["code"] == "TAKE_ENDED"
