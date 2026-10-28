"""Take API — 입력 검증 · 장치 점검 결과."""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from tests.take.conftest import Versions, _make_take

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
    # 짝 없는 서로게이트는 UTF-8 로 바꿀 수 없다
    '[{"type": "slide", "note": "\\ud800"}]',
    '[{"\\udc00": 1}]',
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
        '{"status": "\\ud800"}',
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
