"""리뷰 비교 대상은 같은 발표의 이전 COMPLETED Take 중 번호가 큰 3개다."""

import uuid

import pytest
from sqlalchemy.orm import Session

from pitch_coach_backend.module.take.entity import Take, TakeAIResults
from pitch_coach_backend.module.take.repository import TakeRepository
from pitch_coach_backend.module.take.service.review import _build_previous_takes
from tests.test_take_history import _pitch, _take


def _result(db: Session, take: Take) -> None:
    db.add(TakeAIResults(
        take_id=take.id, kind="TAKE_RESULT", payload={"number": take.take_number}, meta={},
    ))
    db.flush()


@pytest.mark.parametrize("status", ["READY", "RUNNING", "ANALYZING", "FAILED"])
def test_comparison_excludes_unfinished_takes_even_with_a_result(
    db_session: Session, user_id: uuid.UUID, status: str,
) -> None:
    pitch = _pitch(db_session, user_id)
    # 리뷰 실패라도 Take 자체가 COMPLETED 면 측정 결과로 비교할 수 있다.
    finished = _take(db_session, pitch, 1, review_status="FAILED")
    unfinished = _take(
        db_session, pitch, 2, status=status,
        analysis_stage="SCORING" if status == "ANALYZING" else None,
    )
    current = _take(db_session, pitch, 3, status="ANALYZING")
    _result(db_session, finished)
    _result(db_session, unfinished)

    previous = _build_previous_takes(TakeRepository(db_session), current.id, 3)

    assert [take.take_result["number"] for take in previous] == [1]


def test_comparison_filters_before_limit_and_preserves_coverage(
    db_session: Session, user_id: uuid.UUID,
) -> None:
    pitch = _pitch(db_session, user_id)
    for number in range(1, 10):
        take = _take(
            db_session, pitch, number,
            status="COMPLETED" if number <= 4 else "ANALYZING",
        )
        _result(db_session, take)
        if number == 3:
            db_session.add(TakeAIResults(
                take_id=take.id, kind="SCRIPT_COVERAGE",
                payload={"take_scores": {"content_coverage": 0.75}}, meta={},
            ))
    current = _take(db_session, pitch, 10, status="ANALYZING")
    _result(db_session, current)
    _result(db_session, _take(db_session, pitch, 11))
    _result(db_session, _take(db_session, _pitch(db_session, user_id), 9))

    repo = TakeRepository(db_session)
    previous = repo.get_previous_take_results(current.id, 10)

    assert [result["number"] for result, _ in previous] == [4, 3, 2]
    assert previous[0][1] is None
    assert previous[1][1] == {"take_scores": {"content_coverage": 0.75}}
    assert [result["number"] for result, _ in repo.get_previous_take_results(
        current.id, 10, limit=1
    )] == [4]


def test_comparison_is_empty_without_a_completed_result(
    db_session: Session, user_id: uuid.UUID,
) -> None:
    pitch = _pitch(db_session, user_id)
    _take(db_session, pitch, 1)
    current = _take(db_session, pitch, 2, status="ANALYZING")

    assert _build_previous_takes(TakeRepository(db_session), current.id, 2) == []
