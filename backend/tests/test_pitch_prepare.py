"""장치 점검 준비 정보(GET /api/pitches/{pitch_id}/prepare)."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from pitch_coach_backend.core.security import create_access_token
from pitch_coach_backend.module.pitch.entity import (
    Pitch,
    PresentationVersion,
    ScriptSlide,
    ScriptVersion,
)
from pitch_coach_backend.module.take.entity import Mission, Take


@pytest.fixture
def auth_headers(user_id: uuid.UUID) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(user_id)}"}


@pytest.fixture
def other_user_headers(db_session: Session) -> dict[str, str]:
    from pitch_coach_backend.module.user.entity import User

    user = User(email=f"{uuid.uuid4()}@example.com", name="다른 사용자")
    db_session.add(user)
    db_session.flush()
    return {"Authorization": f"Bearer {create_access_token(user.id)}"}


@pytest.fixture
def pitch_id(db_session: Session, user_id: uuid.UUID) -> uuid.UUID:
    pitch = Pitch(user_id=user_id, title="캡스톤 최종 발표", time_limit_sec=600)
    db_session.add(pitch)
    db_session.flush()
    return pitch.id


def _presentation(
    db: Session, pitch_id: uuid.UUID, version: int, page_count: int | None = None
) -> PresentationVersion:
    row = PresentationVersion(
        pitch_id=pitch_id,
        version=version,
        file_key=f"{pitch_id}/{version}.pdf",
        page_count=page_count,
    )
    db.add(row)
    db.flush()
    return row


def _script(
    db: Session, pitch_id: uuid.UUID, version: int, parse_status: str, slides: int = 0
) -> ScriptVersion:
    row = ScriptVersion(
        pitch_id=pitch_id, version=version, content="대본", parse_status=parse_status
    )
    db.add(row)
    db.flush()
    db.add_all(
        ScriptSlide(script_version_id=row.id, slide_number=n, full_content=f"{n}장")
        for n in range(1, slides + 1)
    )
    db.flush()
    return row


def _take(
    db: Session,
    pitch_id: uuid.UUID,
    take_number: int,
    script_mode: str,
    presentation: PresentationVersion,
    script: ScriptVersion,
) -> Take:
    take = Take(
        pitch_id=pitch_id,
        take_number=take_number,
        presentation_version_id=presentation.id,
        script_version_id=script.id,
        mode="COACHING",
        script_mode=script_mode,
        goal_time_sec=600,
    )
    db.add(take)
    db.flush()
    return take


def _reviewed(db: Session, take: Take, completed_at: datetime) -> Take:
    take.status = "COMPLETED"
    take.review_status = "DONE"
    take.completed_at = completed_at
    db.flush()
    return take


def _mission(db: Session, source: Take, priority: int, **fields) -> Mission:
    row = Mission(
        source_take_id=source.id,
        area=fields.pop("area", "GAZE"),
        slide_number=fields.pop("slide_number", None),
        target=fields.pop("target", {"metric": "audience_ratio", "operator": "GTE", "value": 0.6}),
        description=fields.pop("description", f"미션 {priority}"),
        priority=priority,
        **fields,
    )
    db.add(row)
    db.flush()
    return row


def _prepare(client: TestClient, pitch_id: uuid.UUID, headers: dict[str, str]):
    return client.get(f"/api/pitches/{pitch_id}/prepare", headers=headers)


def test_new_pitch_has_no_versions_and_starts_at_take_1(
    client: TestClient, auth_headers: dict[str, str], pitch_id: uuid.UUID
) -> None:
    response = _prepare(client, pitch_id, auth_headers)

    assert response.status_code == 200
    assert response.json() == {
        "pitch_id": str(pitch_id),
        "title": "캡스톤 최종 발표",
        "time_limit_sec": 600,
        "presentation": None,
        "script": None,
        "newer_script": None,
        "next_take_number": 1,
        "default_script_mode": "HIGHLIGHT",
        "missions": None,
    }


def test_picks_latest_presentation_and_latest_parsed_script(
    client: TestClient,
    db_session: Session,
    auth_headers: dict[str, str],
    pitch_id: uuid.UUID,
) -> None:
    _presentation(db_session, pitch_id, 1, page_count=3)
    latest_presentation = _presentation(db_session, pitch_id, 2, page_count=3)
    _script(db_session, pitch_id, 1, "DONE", slides=3)
    parsed = _script(db_session, pitch_id, 2, "DONE", slides=3)

    body = _prepare(client, pitch_id, auth_headers).json()

    assert body["presentation"] == {
        "id": str(latest_presentation.id),
        "version": 2,
        "page_count": 3,
    }
    assert body["script"] == {"id": str(parsed.id), "version": 2, "slide_count": 3}
    assert body["newer_script"] is None


def test_picks_the_latest_presentation_with_as_many_pages_as_slides(
    client: TestClient,
    db_session: Session,
    auth_headers: dict[str, str],
    pitch_id: uuid.UUID,
) -> None:
    """FE 매핑 확인과 같은 규칙. 쪽수를 모르는 발표자료는 짝으로 보지 않는다."""
    _presentation(db_session, pitch_id, 1, page_count=3)
    matching = _presentation(db_session, pitch_id, 2, page_count=3)
    _presentation(db_session, pitch_id, 3, page_count=5)
    _presentation(db_session, pitch_id, 4, page_count=None)
    _script(db_session, pitch_id, 1, "DONE", slides=3)

    body = _prepare(client, pitch_id, auth_headers).json()

    assert body["presentation"]["id"] == str(matching.id)


def test_falls_back_to_the_latest_presentation_when_none_matches(
    client: TestClient,
    db_session: Session,
    auth_headers: dict[str, str],
    pitch_id: uuid.UUID,
) -> None:
    """쪽수와 장 수를 같이 내려서 FE 가 다르다는 걸 보고 시작을 막는다."""
    _presentation(db_session, pitch_id, 1, page_count=4)
    latest = _presentation(db_session, pitch_id, 2, page_count=5)
    _script(db_session, pitch_id, 1, "DONE", slides=3)

    body = _prepare(client, pitch_id, auth_headers).json()

    assert body["presentation"] == {"id": str(latest.id), "version": 2, "page_count": 5}
    assert body["script"]["slide_count"] == 3


def test_skips_scripts_not_parsed_and_reports_the_newest(
    client: TestClient,
    db_session: Session,
    auth_headers: dict[str, str],
    pitch_id: uuid.UUID,
) -> None:
    parsed = _script(db_session, pitch_id, 1, "DONE")
    _script(db_session, pitch_id, 2, "FAILED")
    pending = _script(db_session, pitch_id, 3, "PENDING")

    body = _prepare(client, pitch_id, auth_headers).json()

    assert body["script"] == {"id": str(parsed.id), "version": 1, "slide_count": 0}
    assert body["newer_script"] == {
        "id": str(pending.id),
        "version": 3,
        "parse_status": "PENDING",
        "error_code": None,
    }


def test_newer_script_without_any_parsed_one_is_reported(
    client: TestClient,
    db_session: Session,
    auth_headers: dict[str, str],
    pitch_id: uuid.UUID,
) -> None:
    failed = _script(db_session, pitch_id, 1, "FAILED")
    failed.parse_error = "AI_TIMEOUT"
    db_session.flush()

    body = _prepare(client, pitch_id, auth_headers).json()

    assert body["script"] is None
    assert body["newer_script"] == {
        "id": str(failed.id),
        "version": 1,
        "parse_status": "FAILED",
        "error_code": "AI_TIMEOUT",
    }


def test_stale_pending_script_is_reported_as_expired(
    client: TestClient,
    db_session: Session,
    auth_headers: dict[str, str],
    pitch_id: uuid.UUID,
) -> None:
    """BE 가 재시작돼 끝나지 못한 파싱. 대본 조회(GET /scripts)와 같은 기준으로 FAILED 로 본다."""
    stale = _script(db_session, pitch_id, 1, "PENDING")
    stale.parse_requested_at = datetime.now(UTC) - timedelta(hours=1)
    db_session.flush()

    body = _prepare(client, pitch_id, auth_headers).json()

    assert body["newer_script"]["parse_status"] == "FAILED"
    assert body["newer_script"]["error_code"] == "PARSE_EXPIRED"


def test_next_take_number_and_script_mode_follow_latest_take(
    client: TestClient,
    db_session: Session,
    auth_headers: dict[str, str],
    pitch_id: uuid.UUID,
) -> None:
    presentation = _presentation(db_session, pitch_id, 1)
    script = _script(db_session, pitch_id, 1, "DONE")
    _take(db_session, pitch_id, 1, "HIGHLIGHT", presentation, script)
    _take(db_session, pitch_id, 2, "KEYWORD", presentation, script)

    body = _prepare(client, pitch_id, auth_headers).json()

    assert body["next_take_number"] == 3
    assert body["default_script_mode"] == "KEYWORD"


def test_missions_come_from_the_take_whose_review_finished_last(
    client: TestClient,
    db_session: Session,
    auth_headers: dict[str, str],
    pitch_id: uuid.UUID,
) -> None:
    """Take 를 시작할 때 싣는 미션과 같은 기준이다. 번호가 아니라 리뷰가 늦게 끝난 Take 이고,
    그 Take 가 만든 ACTIVE 미션만 리뷰가 읽는 순서로 준다."""
    presentation = _presentation(db_session, pitch_id, 1)
    script = _script(db_session, pitch_id, 1, "DONE")
    now = datetime.now(UTC)
    later = _take(db_session, pitch_id, 1, "HIGHLIGHT", presentation, script)
    earlier = _take(db_session, pitch_id, 2, "HIGHLIGHT", presentation, script)
    _reviewed(db_session, later, now)
    _reviewed(db_session, earlier, now - timedelta(minutes=1))
    # 아직 분석 중인 Take. 미션은 그 전에 리뷰가 끝난 Take 에서 온다
    _take(db_session, pitch_id, 3, "HIGHLIGHT", presentation, script)
    _mission(db_session, earlier, 1)
    second = _mission(
        db_session,
        later,
        2,
        area="SPEED",
        slide_number=3,
        target={"metric": "cpm", "operator": "LTE", "value": 350},
        description="3번 장을 천천히 말하기",
        reason="3번 장에서 말이 빨랐다",
    )
    first = _mission(db_session, later, 1, description="청중을 보며 말하기")
    _mission(db_session, later, 3, status="ACHIEVED")

    body = _prepare(client, pitch_id, auth_headers).json()

    assert body["next_take_number"] == 4
    assert body["missions"] == {
        "source_take_number": 1,
        "items": [
            {
                "mission_id": str(first.id),
                "priority": 1,
                "area": "GAZE",
                "slide_number": None,
                "description": "청중을 보며 말하기",
                "target": {"metric": "audience_ratio", "operator": "GTE", "value": 0.6},
                "reason": None,
            },
            {
                "mission_id": str(second.id),
                "priority": 2,
                "area": "SPEED",
                "slide_number": 3,
                "description": "3번 장을 천천히 말하기",
                "target": {"metric": "cpm", "operator": "LTE", "value": 350.0},
                "reason": "3번 장에서 말이 빨랐다",
            },
        ],
    }


def test_missions_are_null_until_a_review_finishes(
    client: TestClient,
    db_session: Session,
    auth_headers: dict[str, str],
    pitch_id: uuid.UUID,
) -> None:
    presentation = _presentation(db_session, pitch_id, 1)
    script = _script(db_session, pitch_id, 1, "DONE")
    _take(db_session, pitch_id, 1, "HIGHLIGHT", presentation, script)

    body = _prepare(client, pitch_id, auth_headers).json()

    assert body["missions"] is None


def test_review_without_next_missions_gives_empty_items(
    client: TestClient,
    db_session: Session,
    auth_headers: dict[str, str],
    pitch_id: uuid.UUID,
) -> None:
    presentation = _presentation(db_session, pitch_id, 1)
    script = _script(db_session, pitch_id, 1, "DONE")
    _reviewed(
        db_session,
        _take(db_session, pitch_id, 1, "HIGHLIGHT", presentation, script),
        datetime.now(UTC),
    )

    body = _prepare(client, pitch_id, auth_headers).json()

    assert body["missions"] == {"source_take_number": 1, "items": []}


def test_pitch_of_another_user_is_404(
    client: TestClient, other_user_headers: dict[str, str], pitch_id: uuid.UUID
) -> None:
    response = _prepare(client, pitch_id, other_user_headers)

    assert response.status_code == 404
    assert response.json()["code"] == "PITCH_NOT_FOUND"
