import uuid
from datetime import date

import pytest
from sqlalchemy.orm import Session

from pitch_coach_backend.module.pitch import service
from pitch_coach_backend.module.pitch.dto import PitchDTO
from pitch_coach_backend.module.pitch.entity import PresentationVersion, ScriptVersion
from pitch_coach_backend.module.take.entity import Take, TakeSummary
from pitch_coach_backend.module.user.entity import User


def _make_user(db: Session) -> uuid.UUID:
    user = User(email=f"{uuid.uuid4()}@example.com", name="다른 사용자")
    db.add(user)
    db.flush()
    return user.id


def _make_pitch(db: Session, user_id: uuid.UUID, title: str, time_limit_sec: int) -> uuid.UUID:
    return service.add_pitch_service(
        db,
        user_id,
        PitchDTO(title=title, time_limit_sec=time_limit_sec, presentation_date=date(2026, 3, 1)),
    )


def _make_versions(db: Session, pitch_id: uuid.UUID) -> tuple[uuid.UUID, uuid.UUID]:
    presentation = PresentationVersion(
        pitch_id=pitch_id, version=1, file_key=f"pitches/{pitch_id}/presentations/1.pdf"
    )
    script = ScriptVersion(
        pitch_id=pitch_id, version=1, file_key=f"pitches/{pitch_id}/scripts/1.txt"
    )
    db.add_all([presentation, script])
    db.flush()
    return presentation.id, script.id


def _make_take(
    db: Session,
    pitch_id: uuid.UUID,
    versions: tuple[uuid.UUID, uuid.UUID],
    take_number: int,
    *,
    duration_sec: int = 280,
    goal_time_sec: int = 300,
    script_mode: str = "FULL",
    score: int | None = None,
) -> uuid.UUID:
    presentation_version_id, script_version_id = versions
    take = Take(
        pitch_id=pitch_id,
        take_number=take_number,
        presentation_version_id=presentation_version_id,
        script_version_id=script_version_id,
        mode="PRACTICE",
        script_mode=script_mode,
        status="COMPLETED",
        duration_sec=duration_sec,
        goal_time_sec=goal_time_sec,
    )
    db.add(take)
    db.flush()

    if score is not None:
        db.add(
            TakeSummary(
                take_id=take.id,
                average_wpm=120.0,
                filler_count=3,
                audience_gaze=50,
                slide_gaze=30,
                script_gaze=20,
                script_dependency_count=2,
                summary="요약",
                overall_confidence=0.8,
                speak_accuracy=0.9,
                volume=55.5,
                total_intermission=1,
                score=score,
            )
        )
        db.flush()

    return take.id


@pytest.fixture
def pitch_id(db_session: Session, user_id: uuid.UUID) -> uuid.UUID:
    return _make_pitch(db_session, user_id, "기존 발표", 300)


@pytest.fixture
def versions(db_session: Session, pitch_id: uuid.UUID) -> tuple[uuid.UUID, uuid.UUID]:
    return _make_versions(db_session, pitch_id)

# pitch가 없는 경우 빈 리스트를 반환하는지 확인
def test_returns_empty_list_when_user_has_no_pitch(
    db_session: Session, user_id: uuid.UUID
) -> None:
    assert service.get_all_pitches_service(db_session, user_id) == []

# pitch 필드가 잘 매핑되는지 확인
def test_maps_pitch_fields(db_session: Session, user_id: uuid.UUID, pitch_id: uuid.UUID) -> None:
    result = service.get_all_pitches_service(db_session, user_id)

    assert len(result) == 1
    assert result[0].pitch_title == "기존 발표"
    assert result[0].pitch_time == 300
    assert result[0].thumbnail_url is None

# pitch에 take가 없는 경우 takes가 빈 리스트로 나오는지 확인
def test_pitch_without_takes_has_empty_takes(
    db_session: Session, user_id: uuid.UUID, pitch_id: uuid.UUID
) -> None:
    result = service.get_all_pitches_service(db_session, user_id)

    assert result[0].takes == []

## take 관련 필드가 잘 매핑되는지 확인
def test_maps_take_fields(
    db_session: Session,
    user_id: uuid.UUID,
    pitch_id: uuid.UUID,
    versions: tuple[uuid.UUID, uuid.UUID],
) -> None:
    _make_take(
        db_session,
        pitch_id,
        versions,
        1,
        duration_sec=275,
        goal_time_sec=300,
        script_mode="KEYWORD",
        score=71,
    )

    result = service.get_all_pitches_service(db_session, user_id)

    take = result[0].takes[0]
    assert take.take_version == 1
    assert take.take_elapsed == 275
    assert take.take_time == 300
    assert take.script_mode == "KEYWORD"
    assert take.score == 71

# delta값이 잘 나오는지
def test_delta_is_none_on_first_take_and_diff_from_previous_afterwards(
    db_session: Session,
    user_id: uuid.UUID,
    pitch_id: uuid.UUID,
    versions: tuple[uuid.UUID, uuid.UUID],
) -> None:
    _make_take(db_session, pitch_id, versions, 1, score=60)
    _make_take(db_session, pitch_id, versions, 2, score=75)
    _make_take(db_session, pitch_id, versions, 3, score=70)

    result = service.get_all_pitches_service(db_session, user_id)

    assert [t.take_version for t in result[0].takes] == [1, 2, 3]
    assert [t.score for t in result[0].takes] == [60, 75, 70]
    assert [t.delta for t in result[0].takes] == [None, 15, -5]

# take_number 순서대로 정렬되는지 확인.
def test_takes_are_ordered_by_take_number_not_insertion_order(
    db_session: Session,
    user_id: uuid.UUID,
    pitch_id: uuid.UUID,
    versions: tuple[uuid.UUID, uuid.UUID],
) -> None:
    _make_take(db_session, pitch_id, versions, 3, score=90)
    _make_take(db_session, pitch_id, versions, 1, score=50)
    _make_take(db_session, pitch_id, versions, 2, score=70)

    result = service.get_all_pitches_service(db_session, user_id)

    assert [t.take_version for t in result[0].takes] == [1, 2, 3]
    assert [t.delta for t in result[0].takes] == [None, 20, 20]

# TakeSummary가 없는 경우 score와 delta가 null이어야 함
def test_take_without_summary_has_null_score_and_null_delta(
    db_session: Session,
    user_id: uuid.UUID,
    pitch_id: uuid.UUID,
    versions: tuple[uuid.UUID, uuid.UUID],
) -> None:
    _make_take(db_session, pitch_id, versions, 1, score=60)
    _make_take(db_session, pitch_id, versions, 2, score=None)
    _make_take(db_session, pitch_id, versions, 3, score=80)

    result = service.get_all_pitches_service(db_session, user_id)

    assert [t.score for t in result[0].takes] == [60, None, 80]
    assert [t.delta for t in result[0].takes] == [None, None, None]

# 완료되지 않은 경우에 대한 등록(완료되지 않은 Take를 버린다면 이 테스트는 삭제할 예정)
def test_take_that_has_not_finished_has_null_elapsed(
    db_session: Session,
    user_id: uuid.UUID,
    pitch_id: uuid.UUID,
    versions: tuple[uuid.UUID, uuid.UUID],
) -> None:
    take = Take(
        pitch_id=pitch_id,
        take_number=1,
        presentation_version_id=versions[0],
        script_version_id=versions[1],
        mode="PRACTICE",
        script_mode="FULL",
        status="READY",
        goal_time_sec=300,
    )
    db_session.add(take)
    db_session.flush()

    result = service.get_all_pitches_service(db_session, user_id)

    assert result[0].takes[0].take_elapsed is None
    assert result[0].takes[0].take_time == 300

# user_id가 아닌 다른 사용자의 pitch는 조회되지 않아야 함
def test_excludes_pitches_of_other_users(
    db_session: Session, user_id: uuid.UUID, pitch_id: uuid.UUID
) -> None:
    other_user_id = _make_user(db_session)
    _make_pitch(db_session, other_user_id, "남의 발표", 600)

    result = service.get_all_pitches_service(db_session, user_id)

    assert [p.pitch_title for p in result] == ["기존 발표"]


# pitch별로 take가 묶여서 나오는지 확인
def test_takes_are_grouped_under_their_own_pitch(
    db_session: Session, user_id: uuid.UUID, pitch_id: uuid.UUID
) -> None:
    second_pitch_id = _make_pitch(db_session, user_id, "두번째 발표", 420)
    _make_take(db_session, pitch_id, _make_versions(db_session, pitch_id), 1, score=60)
    second_versions = _make_versions(db_session, second_pitch_id)
    _make_take(db_session, second_pitch_id, second_versions, 1, score=80)
    _make_take(db_session, second_pitch_id, second_versions, 2, score=85)

    result = service.get_all_pitches_service(db_session, user_id)

    by_title = {p.pitch_title: p for p in result}
    assert [t.score for t in by_title["기존 발표"].takes] == [60]
    assert [t.score for t in by_title["두번째 발표"].takes] == [80, 85]
