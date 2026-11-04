"""Take 목록 API — GET /api/takes/ (Take 기록 · Take 비교 화면)."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from pitch_coach_backend.core.security import create_access_token
from pitch_coach_backend.module.pitch.entity import Pitch, PresentationVersion, ScriptVersion
from pitch_coach_backend.module.take.coach_repository import CoachRepository
from pitch_coach_backend.module.take.entity import (
    Calibration,
    Mission,
    Take,
    TakeAIResults,
    TakeSummary,
)
from pitch_coach_backend.module.user.entity import User

T0 = datetime(2026, 10, 20, 9, 0, tzinfo=UTC)


@pytest.fixture
def auth_headers(user_id: uuid.UUID) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(user_id)}"}


def _pitch(db: Session, user_id: uuid.UUID, title: str = "발표") -> Pitch:
    pitch = Pitch(user_id=user_id, title=title, time_limit_sec=300)
    db.add(pitch)
    db.flush()
    db.add_all([
        PresentationVersion(pitch_id=pitch.id, version=1, file_key=f"{pitch.id}/1.pdf"),
        ScriptVersion(pitch_id=pitch.id, version=1, content="대본"),
    ])
    db.flush()
    return pitch


def _take(
    db: Session,
    pitch: Pitch,
    take_number: int,
    *,
    status: str = "COMPLETED",
    started_at: datetime | None = T0,
    **values: object,
) -> Take:
    presentation = db.query(PresentationVersion).filter_by(pitch_id=pitch.id).one()
    script = db.query(ScriptVersion).filter_by(pitch_id=pitch.id).one()
    take = Take(
        pitch_id=pitch.id,
        take_number=take_number,
        presentation_version_id=presentation.id,
        script_version_id=script.id,
        mode="COACHING",
        script_mode="HIGHLIGHT",
        goal_time_sec=300,
        status=status,
        started_at=started_at,
        **values,
    )
    db.add(take)
    db.flush()
    return take


def _list(client: TestClient, headers: dict[str, str], pitch_id: uuid.UUID | None = None):
    query = "" if pitch_id is None else f"?pitch_id={pitch_id}"
    return client.get(f"/api/takes/{query}", headers=headers)


def _review(db: Session, take: Take, results: list[dict[str, str]]) -> None:
    db.add(TakeAIResults(
        take_id=take.id, kind="REVIEW", payload={"mission_results": results}, meta={},
    ))
    db.flush()


def test_lists_measures_missions_and_gaze_engine(
    client: TestClient, db_session: Session, user_id: uuid.UUID, auth_headers: dict[str, str]
) -> None:
    pitch = _pitch(db_session, user_id, "투자 발표")
    source = _take(db_session, pitch, 1)
    missions = [
        Mission(
            source_take_id=source.id,
            area="GAZE",
            target={"metric": "audience_ratio"},
            description=f"미션 {priority}",
            priority=priority,
            status=status,
        )
        for priority, status in ((1, "ACHIEVED"), (2, "FAILED"))
    ]
    db_session.add_all(missions)
    db_session.flush()
    take = _take(
        db_session,
        pitch,
        2,
        started_at=T0 + timedelta(days=1),
        duration_sec=282,
        mission_ids=[str(m.id) for m in missions],
    )
    for mission in missions:
        mission.take_id = take.id
    _review(db_session, take, [
        {"mission_id": str(m.id), "status": m.status} for m in missions
    ])
    db_session.add_all([
        TakeSummary(take_id=take.id, audience_gaze=74, script_gaze=22, average_cpm=352.5,
                    filler_count=7),
        Calibration(take_id=take.id, base_volume=0, gaze_confidence=0.9,
                    result={"engine_version": "gaze_v1.1.0"}),
    ])
    db_session.flush()

    response = _list(client, auth_headers)

    assert response.status_code == 200
    row = response.json()["takes"][0]
    assert row == {
        "take_id": str(take.id),
        "pitch_id": str(pitch.id),
        "pitch_title": "투자 발표",
        "take_number": 2,
        "started_at": row["started_at"],
        "status": "COMPLETED",
        "end_reason": None,
        "script_mode": "HIGHLIGHT",
        "duration_ms": 282_000,
        "goal_ms": 300_000,
        "audience_ratio": 0.74,
        "script_ratio": 0.22,
        "cpm": 352.5,
        "filler_count": 7,
        "missions_achieved": 1,
        "missions_total": 2,
        "gaze_engine": "gaze_v1.1.0",
    }
    assert datetime.fromisoformat(row["started_at"]) == T0 + timedelta(days=1)


@pytest.mark.parametrize("end_reason", [None, "SILENCE", "TIME_LIMIT", "ABANDONED", "CLOSED"])
def test_lists_end_reason(
    client: TestClient, db_session: Session, user_id: uuid.UUID,
    auth_headers: dict[str, str], end_reason: str | None,
) -> None:
    status = "FAILED" if end_reason in ("ABANDONED", "CLOSED") else "COMPLETED"
    _take(db_session, _pitch(db_session, user_id), 1, status=status, end_reason=end_reason)

    [row] = _list(client, auth_headers).json()["takes"]

    assert row["end_reason"] == end_reason


def test_mission_verdict_stays_with_the_take_when_a_later_take_starts(
    client: TestClient, db_session: Session, user_id: uuid.UUID, auth_headers: dict[str, str]
) -> None:
    pitch = _pitch(db_session, user_id)
    source = _take(db_session, pitch, 1)
    earlier = _take(db_session, pitch, 2)
    later = _take(db_session, pitch, 3, status="RUNNING")
    mission = Mission(
        source_take_id=source.id, take_id=later.id, area="GAZE",
        target={"metric": "audience_ratio"}, description="청중 보기", priority=1,
        status="ACTIVE",
    )
    db_session.add(mission)
    db_session.flush()
    earlier.mission_ids = later.mission_ids = [str(mission.id)]
    _review(db_session, earlier, [{"mission_id": str(mission.id), "status": "ACHIEVED"}])

    def counts():
        response = _list(client, auth_headers)
        assert response.status_code == 200
        return {row["take_id"]: row["missions_achieved"] for row in response.json()["takes"]}

    assert counts()[str(earlier.id)] == 1
    assert counts()[str(later.id)] == 0
    # 뒤 Take 의 판정이 공유 미션 행을 바꿔도 앞 기록은 그대로다.
    mission.status = "FAILED"
    _review(db_session, later, [{"mission_id": str(mission.id), "status": "FAILED"}])
    assert counts()[str(earlier.id)] == 1
    assert counts()[str(later.id)] == 0


def test_mission_count_uses_unique_achieved_ids_from_the_frozen_list(
    client: TestClient, db_session: Session, user_id: uuid.UUID, auth_headers: dict[str, str]
) -> None:
    pitch = _pitch(db_session, user_id)
    achieved, failed, unrelated = (str(uuid.uuid4()) for _ in range(3))
    take = _take(db_session, pitch, 1, mission_ids=[achieved, failed])
    _review(db_session, take, [
        {"mission_id": achieved, "status": "ACHIEVED"},
        {"mission_id": achieved, "status": "ACHIEVED"},
        {"mission_id": failed, "status": "FAILED"},
        {"mission_id": unrelated, "status": "ACHIEVED"},
    ])

    [row] = _list(client, auth_headers).json()["takes"]

    assert row["missions_achieved"] == 1
    assert row["missions_total"] == 2


def test_mission_without_a_saved_review_does_not_count_as_achieved(
    client: TestClient, db_session: Session, user_id: uuid.UUID, auth_headers: dict[str, str]
) -> None:
    pitch = _pitch(db_session, user_id)
    source = _take(db_session, pitch, 1)
    take = _take(db_session, pitch, 2)
    mission = Mission(
        source_take_id=source.id, take_id=take.id, area="GAZE", target={},
        description="미션", priority=1, status="ACHIEVED",
    )
    db_session.add(mission)
    db_session.flush()
    take.mission_ids = [str(mission.id)]
    db_session.flush()

    rows = {r["take_id"]: r for r in _list(client, auth_headers).json()["takes"]}

    assert rows[str(take.id)]["missions_achieved"] == 0


@pytest.mark.parametrize("payload", [{}, {"mission_results": None}])
def test_old_review_without_mission_verdicts_counts_zero(
    client: TestClient, db_session: Session, user_id: uuid.UUID,
    auth_headers: dict[str, str], payload: dict,
) -> None:
    take = _take(
        db_session, _pitch(db_session, user_id), 1, mission_ids=[str(uuid.uuid4())]
    )
    db_session.add(TakeAIResults(take_id=take.id, kind="REVIEW", payload=payload, meta={}))
    db_session.flush()

    response = _list(client, auth_headers)

    assert response.status_code == 200
    [row] = response.json()["takes"]
    assert row["missions_achieved"] == 0
    assert row["missions_total"] == 1


def test_unmeasured_values_are_null(
    client: TestClient, db_session: Session, user_id: uuid.UUID, auth_headers: dict[str, str]
) -> None:
    # 발표 중이라 끝난 시간 · 집계 · 장치 점검 결과 · 실은 미션이 없다
    take = _take(db_session, _pitch(db_session, user_id), 1, status="RUNNING")

    [row] = _list(client, auth_headers).json()["takes"]

    assert row["take_id"] == str(take.id)
    assert row["duration_ms"] is None
    assert row["audience_ratio"] is None
    assert row["script_ratio"] is None
    assert row["cpm"] is None
    assert row["filler_count"] is None
    assert row["missions_achieved"] == 0
    assert row["missions_total"] == 0
    assert row["gaze_engine"] is None


def test_newest_first_across_pitches_and_skips_unstarted(
    client: TestClient, db_session: Session, user_id: uuid.UUID, auth_headers: dict[str, str]
) -> None:
    first, second = _pitch(db_session, user_id, "A"), _pitch(db_session, user_id, "B")
    old = _take(db_session, first, 1, started_at=T0)
    newest = _take(db_session, second, 1, started_at=T0 + timedelta(hours=2))
    middle = _take(db_session, first, 2, started_at=T0 + timedelta(hours=1))
    # 장치 점검에서 버려진 Take — 시작 시각이 없다
    _take(db_session, first, 3, status="READY", started_at=None)
    _take(db_session, first, 4, status="FAILED", started_at=None, end_reason="ABANDONED")

    takes = _list(client, auth_headers).json()["takes"]

    assert [t["take_id"] for t in takes] == [str(newest.id), str(middle.id), str(old.id)]


def test_pitch_id_filters_to_that_pitch(
    client: TestClient, db_session: Session, user_id: uuid.UUID, auth_headers: dict[str, str]
) -> None:
    pitch, other = _pitch(db_session, user_id), _pitch(db_session, user_id)
    take = _take(db_session, pitch, 1)
    _take(db_session, other, 1)

    takes = _list(client, auth_headers, pitch.id).json()["takes"]

    assert [t["take_id"] for t in takes] == [str(take.id)]


def test_other_users_takes_are_not_listed(
    client: TestClient, db_session: Session, user_id: uuid.UUID, auth_headers: dict[str, str]
) -> None:
    stranger = User(email=f"{uuid.uuid4()}@example.com", name="다른 사용자")
    db_session.add(stranger)
    db_session.flush()
    others = _pitch(db_session, stranger.id)
    _take(db_session, others, 1)

    assert _list(client, auth_headers).json() == {"takes": []}
    # 남의 발표와 없는 발표는 구분하지 않는다
    assert _list(client, auth_headers, others.id).status_code == 404
    assert _list(client, auth_headers, uuid.uuid4()).status_code == 404


def test_requires_login(client: TestClient) -> None:
    assert client.get("/api/takes/").status_code == 401


# ── 시작 시각 ──────────────────────────────────────────────────────


def test_first_connection_records_started_at(db_session: Session, user_id: uuid.UUID) -> None:
    take = _take(db_session, _pitch(db_session, user_id), 1, status="READY", started_at=None)

    assert CoachRepository(db_session).mark_started(take.id) == 1
    db_session.refresh(take)

    assert take.status == "RUNNING"
    assert take.started_at is not None


def test_first_connection_keeps_started_at_already_written(
    db_session: Session, user_id: uuid.UUID
) -> None:
    take = _take(db_session, _pitch(db_session, user_id), 1, status="READY", started_at=T0)

    CoachRepository(db_session).mark_started(take.id)
    db_session.refresh(take)

    assert take.started_at == T0
