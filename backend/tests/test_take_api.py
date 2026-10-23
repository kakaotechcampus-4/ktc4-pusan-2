"""Take API — 이전 미션 조회, 부분 수정(PUT), 입력 검증."""

import uuid
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from pitch_coach_backend.core.security import create_access_token
from pitch_coach_backend.module.pitch.entity import Pitch, PresentationVersion, ScriptVersion
from pitch_coach_backend.module.take.entity import Mission, Take
from pitch_coach_backend.module.take.repository import TakeRepository

Versions = tuple[uuid.UUID, uuid.UUID]


@pytest.fixture
def auth_headers(user_id: uuid.UUID) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(user_id)}"}


@pytest.fixture
def pitch_id(db_session: Session, user_id: uuid.UUID) -> uuid.UUID:
    pitch = Pitch(user_id=user_id, title="Take API", time_limit_sec=300)
    db_session.add(pitch)
    db_session.flush()
    return pitch.id


@pytest.fixture
def versions(db_session: Session, pitch_id: uuid.UUID) -> Versions:
    presentation = PresentationVersion(pitch_id=pitch_id, version=1, file_key=f"{pitch_id}/1.pdf")
    script = ScriptVersion(pitch_id=pitch_id, version=1, content="대본")
    db_session.add_all([presentation, script])
    db_session.flush()
    return presentation.id, script.id


def _make_take(db: Session, pitch_id: uuid.UUID, versions: Versions, take_number: int) -> Take:
    take = Take(
        pitch_id=pitch_id,
        take_number=take_number,
        presentation_version_id=versions[0],
        script_version_id=versions[1],
        mode="COACHING",
        script_mode="HIGHLIGHT",
        goal_time_sec=300,
    )
    db.add(take)
    db.flush()
    return take


def _missions(client: TestClient, pitch_id: uuid.UUID, headers: dict[str, str]):
    return client.get(f"/api/pitches/{pitch_id}/takes/previous-missions", headers=headers)


# ── 이전 미션 ──────────────────────────────────────────────────────


def test_previous_missions_without_take_is_empty(
    client: TestClient, auth_headers: dict[str, str], pitch_id: uuid.UUID
) -> None:
    response = _missions(client, pitch_id, auth_headers)

    assert response.status_code == 200
    assert response.json()["missions"] == {
        "source_take_id": None,
        "next_take_number": 1,
        "missions": [],
    }


def test_previous_missions_come_from_latest_take(
    client: TestClient,
    db_session: Session,
    auth_headers: dict[str, str],
    pitch_id: uuid.UUID,
    versions: Versions,
) -> None:
    old = _make_take(db_session, pitch_id, versions, 1)
    latest = _make_take(db_session, pitch_id, versions, 2)
    old.review_status = "DONE"
    latest.review_status = "DONE"
    db_session.add(
        Mission(
            source_take_id=old.id,
            area="FILLER",
            slide_number=1,
            target={"metric": "filler_per_min", "operator": "LTE", "value": 2},
            description="옛것",
            priority=1,
        )
    )
    second = Mission(
        source_take_id=latest.id,
        area="GAZE",
        slide_number=3,
        target={"metric": "script_ratio", "operator": "LTE", "value": 0.4},
        description="시선",
        priority=2,
    )
    first = Mission(
        source_take_id=latest.id,
        area="SPEED",
        slide_number=5,
        target={"metric": "cpm", "operator": "LTE", "value": 350},
        description="속도",
        priority=1,
    )
    db_session.add_all([second, first])
    db_session.flush()

    response = _missions(client, pitch_id, auth_headers)

    assert response.status_code == 200
    body = response.json()["missions"]
    assert body["source_take_id"] == str(latest.id)
    assert body["next_take_number"] == 3
    assert body["missions"] == [
        {
            "mission_id": str(first.id),
            "slide_number": 5,
            "description": "속도",
            "priority": 1,
            "completed": False,
        },
        {
            "mission_id": str(second.id),
            "slide_number": 3,
            "description": "시선",
            "priority": 2,
            "completed": False,
        },
    ]


def test_latest_take_lookup_leaves_event_logs_unloaded(
    db_session: Session, pitch_id: uuid.UUID, versions: Versions
) -> None:
    """장치 점검 · 이전 미션은 번호만 본다. 시선 1초 기록이 쌓인 event_logs 는 읽지 않는다."""
    take = _make_take(db_session, pitch_id, versions, 1)
    take.event_logs = [{"type": "gaze"}]
    db_session.flush()
    db_session.expire_all()

    latest = TakeRepository(db_session).get_latest_take_in_pitch(pitch_id)

    assert latest is not None
    assert "event_logs" in inspect(latest).unloaded


def test_previous_missions_of_take_without_missions_is_empty_list(
    client: TestClient,
    db_session: Session,
    auth_headers: dict[str, str],
    pitch_id: uuid.UUID,
    versions: Versions,
) -> None:
    _make_take(db_session, pitch_id, versions, 1)

    response = _missions(client, pitch_id, auth_headers)

    assert response.status_code == 200
    assert response.json()["missions"]["missions"] == []


# ── PUT /takes/{take_id} ────────────────────────────────────────────


def _put(client: TestClient, take: Take, headers: dict[str, str], body: dict):
    return client.put(f"/api/pitches/{take.pitch_id}/takes/{take.id}", json=body, headers=headers)


def test_put_keeps_fields_that_were_not_sent(
    client: TestClient,
    db_session: Session,
    auth_headers: dict[str, str],
    pitch_id: uuid.UUID,
    versions: Versions,
) -> None:
    take = _make_take(db_session, pitch_id, versions, 1)
    started = "2026-10-07T10:00:00+09:00"
    ended = "2026-10-07T10:05:00+09:00"
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
    take = _make_take(db_session, pitch_id, versions, 1)

    assert _put(client, take, auth_headers, {"ended_at": "2026-10-07T01:05:00Z"}).is_success

    db_session.refresh(take)
    assert take.ended_at == datetime(2026, 10, 7, 1, 5, tzinfo=UTC)


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
    take = _make_take(db_session, pitch_id, versions, 1)
    started = "2026-10-07T10:00:00+09:00"
    before_start = "2026-10-07T09:59:00+09:00"

    together = _put(client, take, auth_headers, {"started_at": started, "ended_at": before_start})
    assert _put(client, take, auth_headers, {"started_at": started}).is_success
    separately = _put(client, take, auth_headers, {"ended_at": before_start})

    for response in (together, separately):
        assert response.status_code == 400
        assert response.json()["code"] == "INVALID_TAKE_TIME"
    db_session.refresh(take)
    assert take.ended_at is None


def test_put_null_does_not_erase_a_field(
    client: TestClient,
    db_session: Session,
    auth_headers: dict[str, str],
    pitch_id: uuid.UUID,
    versions: Versions,
) -> None:
    take = _make_take(db_session, pitch_id, versions, 1)
    started = "2026-10-07T10:00:00+09:00"

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

    response = _put(client, take, auth_headers, {"ended_at": "2026-10-07T10:05:00+09:00"})

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
    started = "2026-10-07T10:00:00+09:00"
    ended = "2026-10-07T10:05:00+09:00"

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


@pytest.mark.parametrize("status", ["READY", "RUNNING"])
def test_put_end_finishes_a_take_that_never_sent_stop(
    client: TestClient,
    db_session: Session,
    auth_headers: dict[str, str],
    pitch_id: uuid.UUID,
    versions: Versions,
    status: str,
) -> None:
    """마이크를 못 열었거나 재연결을 포기한 FE 는 WS stop 을 못 보낸다. 시각을 보내는 PUT 이
    Take 를 끝내야 리포트 분석이 시작된다. 인증 전에 끊겼으면 READY 그대로다."""
    take = _make_take(db_session, pitch_id, versions, 1)
    take.status = status
    db_session.flush()
    ended = "2026-10-07T10:05:00+09:00"

    response = _put(
        client, take, auth_headers, {"started_at": "2026-10-07T10:00:00+09:00", "ended_at": ended}
    )

    assert response.is_success
    db_session.refresh(take)
    assert (take.status, take.ended_at) == ("ANALYZING", datetime.fromisoformat(ended))


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
    take.ended_at = datetime(2026, 10, 7, 1, 1, tzinfo=UTC)
    db_session.flush()
    ended = "2026-10-07T10:50:00+09:00"
    logs = [{"type": "slide", "at_ms": 0}]

    early = _put(client, take, auth_headers, {"event_logs": logs})
    response = _put(
        client,
        take,
        auth_headers,
        {"started_at": "2026-10-07T10:00:00+09:00", "ended_at": ended, "event_logs": logs},
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


# ── DELETE /takes/{take_id} ─────────────────────────────────────────


def test_delete_take_without_a_live_stream(
    client: TestClient,
    db_session: Session,
    auth_headers: dict[str, str],
    pitch_id: uuid.UUID,
    versions: Versions,
) -> None:
    """실시간 STT 가 붙어 있지 않은 Take 도 그대로 지워진다 (스트림 끊기는 건너뛴다)."""
    take_id = _make_take(db_session, pitch_id, versions, 1).id

    response = client.delete(f"/api/pitches/{pitch_id}/takes/{take_id}", headers=auth_headers)

    assert response.status_code == 200
    assert db_session.get(Take, take_id) is None


# ── 입력 검증 ───────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("field", "value"), [("mode", "PRACTICE"), ("mode", "coaching"), ("script_mode", "FULL")]
)
def test_create_with_unknown_mode_is_422(
    client: TestClient,
    auth_headers: dict[str, str],
    pitch_id: uuid.UUID,
    versions: Versions,
    field: str,
    value: str,
) -> None:
    body = {
        "mode": "COACHING",
        "script_mode": "HIGHLIGHT",
        "presentation_version_id": str(versions[0]),
        "script_version_id": str(versions[1]),
    }
    body[field] = value

    response = client.post(f"/api/pitches/{pitch_id}/takes/", json=body, headers=auth_headers)

    assert response.status_code == 422


@pytest.mark.parametrize(
    ("column", "value"),
    [("status", "DONE"), ("mode", "PRACTICE"), ("script_mode", "FULL"), ("end_reason", "STOP")],
)
def test_takes_table_rejects_unknown_values(
    db_session: Session, pitch_id: uuid.UUID, versions: Versions, column: str, value: str
) -> None:
    """DTO 를 거치지 않는 저장도 테이블의 CHECK 가 막는다."""
    take = _make_take(db_session, pitch_id, versions, 1)
    setattr(take, column, value)

    with pytest.raises(IntegrityError):
        db_session.flush()


@pytest.mark.parametrize(
    ("base_volume", "status"), [(999.99, 200), (999.995, 422), (1000, 422), (-1, 422)]
)
def test_calibration_base_volume_range(
    client: TestClient,
    db_session: Session,
    auth_headers: dict[str, str],
    pitch_id: uuid.UUID,
    versions: Versions,
    base_volume: float,
    status: int,
) -> None:
    take = _make_take(db_session, pitch_id, versions, 1)

    response = client.post(
        f"/api/pitches/{pitch_id}/takes/{take.id}/calibration",
        json={"base_volume": base_volume},
        headers=auth_headers,
    )

    assert response.status_code == status


# PostgreSQL 이 JSONB 에 넣지 못하는 값. httpx 는 NaN 을 직렬화하지 않아 본문을 문자열로 보낸다
_UNSTORABLE_EVENT_LOGS = [
    '[{"type": "slide", "note": "\\u0000"}]',
    '[{"\\u0000": 1}]',
    '[{"type": "slide", "at_ms": NaN}]',
    '[{"type": "slide", "nested": [{"value": Infinity}]}]',
]


@pytest.mark.parametrize("event_logs", _UNSTORABLE_EVENT_LOGS)
def test_put_with_unstorable_event_logs_is_422(
    client: TestClient,
    db_session: Session,
    auth_headers: dict[str, str],
    pitch_id: uuid.UUID,
    versions: Versions,
    event_logs: str,
) -> None:
    take = _make_take(db_session, pitch_id, versions, 1)

    response = client.put(
        f"/api/pitches/{pitch_id}/takes/{take.id}",
        content=f'{{"event_logs": {event_logs}}}',
        headers={**auth_headers, "Content-Type": "application/json"},
    )

    assert response.status_code == 422


@pytest.mark.parametrize(
    "result",
    [
        '{"engine_version": "1.0\\u0000"}',
        '{"status": "\\u0000"}',
        '{"warnings": ["ok", "\\u0000"]}',
        '{"separability": NaN}',
        '{"loo_accuracy": Infinity}',
        '{"loo_accuracy": -Infinity}',
    ],
)
def test_calibration_with_unstorable_result_is_422(
    client: TestClient,
    db_session: Session,
    auth_headers: dict[str, str],
    pitch_id: uuid.UUID,
    versions: Versions,
    result: str,
) -> None:
    take = _make_take(db_session, pitch_id, versions, 1)

    response = client.post(
        f"/api/pitches/{pitch_id}/takes/{take.id}/calibration",
        content=f'{{"result": {result}}}',
        headers={**auth_headers, "Content-Type": "application/json"},
    )

    assert response.status_code == 422


@pytest.mark.parametrize("status", ["RUNNING", "ANALYZING", "COMPLETED"])
def test_calibration_after_start_is_409(
    client: TestClient,
    db_session: Session,
    auth_headers: dict[str, str],
    pitch_id: uuid.UUID,
    versions: Versions,
    status: str,
) -> None:
    """점검 결과는 발표 전에만 받는다. 시작한 Take 의 기준값을 바꾸면 분석 기준이 바뀐다."""
    from pitch_coach_backend.module.take.entity import Calibration

    take = _make_take(db_session, pitch_id, versions, 1)
    take.status = status
    db_session.flush()

    response = client.post(
        f"/api/pitches/{pitch_id}/takes/{take.id}/calibration",
        json={"base_level_db": -30.0},
        headers=auth_headers,
    )

    assert response.status_code == 409
    assert response.json()["code"] == "TAKE_STARTED"
    assert db_session.query(Calibration).filter_by(take_id=take.id).count() == 0


def test_calibration_is_saved(
    client: TestClient,
    db_session: Session,
    auth_headers: dict[str, str],
    pitch_id: uuid.UUID,
    versions: Versions,
) -> None:
    """gaze_confidence 를 bool 로 받던 때는 Float 컬럼에 넣다가 항상 500 이었다."""
    from pitch_coach_backend.module.take.entity import Calibration

    take = _make_take(db_session, pitch_id, versions, 1)

    response = client.post(
        f"/api/pitches/{pitch_id}/takes/{take.id}/calibration",
        json={"face_detected": True, "base_volume": 42.5, "gaze_confidence": 0.82},
        headers=auth_headers,
    )

    assert response.status_code == 200
    saved = db_session.get(Calibration, uuid.UUID(response.json()["calibration_id"]))
    assert saved is not None
    assert (saved.face_detected, float(saved.base_volume), saved.gaze_confidence) == (
        True,
        42.5,
        0.82,
    )
