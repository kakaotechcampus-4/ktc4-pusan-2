"""리뷰의 반복 문제는 저장 리뷰의 완료 시각 순서로 고른다."""

import uuid
from datetime import timedelta

import pytest
from sqlalchemy.orm import Session

from pitch_coach_backend.module.take.entity import Take, TakeAIResults
from pitch_coach_backend.module.take.repository import TakeRepository
from pitch_coach_backend.module.take.service import _build_previous_recurring_issues
from tests.test_take_history import T0, _pitch, _take


def _review(db: Session, take: Take) -> None:
    db.add(TakeAIResults(
        take_id=take.id, kind="REVIEW",
        payload={"recurring_issues": [{"number": take.take_number}]}, meta={},
    ))
    db.flush()


@pytest.mark.parametrize(
    ("first_at", "second_at", "expected"),
    [
        (T0 + timedelta(hours=2), T0 + timedelta(hours=1), 1),
        (T0, T0, 2),
        (T0, None, 1),
        (None, None, 2),
    ],
    ids=["completion-order", "tied-completion", "null-last", "both-legacy"],
)
def test_recurring_issues_use_completion_order_with_number_tiebreak(
    db_session: Session, user_id: uuid.UUID, first_at, second_at, expected: int,
) -> None:
    pitch = _pitch(db_session, user_id)
    first = _take(db_session, pitch, 1, completed_at=first_at, review_status="DONE")
    second = _take(db_session, pitch, 2, completed_at=second_at, review_status="DONE")
    current = _take(db_session, pitch, 3, status="ANALYZING")
    _review(db_session, first)
    _review(db_session, second)

    previous = _build_previous_recurring_issues(TakeRepository(db_session), current.id, 3)

    assert previous == [{"number": expected}]


def test_recurring_issues_fall_back_to_latest_saved_review_in_the_same_pitch(
    db_session: Session, user_id: uuid.UUID,
) -> None:
    pitch = _pitch(db_session, user_id)
    reviewed = _take(db_session, pitch, 1, completed_at=T0, review_status="DONE")
    _review(db_session, reviewed)
    _take(db_session, pitch, 2, completed_at=T0 + timedelta(hours=1), review_status="FAILED")
    current = _take(db_session, pitch, 3, status="ANALYZING")
    # 자신 이후 번호와 다른 발표는 대상에서 빠진다.
    _review(db_session, _take(db_session, pitch, 4, completed_at=T0 + timedelta(hours=2)))
    _review(db_session, _take(
        db_session, _pitch(db_session, user_id), 2, completed_at=T0 + timedelta(hours=3),
    ))

    previous = _build_previous_recurring_issues(TakeRepository(db_session), current.id, 3)

    assert previous == [{"number": 1}]


def test_recurring_issues_are_empty_without_a_saved_review(
    db_session: Session, user_id: uuid.UUID,
) -> None:
    pitch = _pitch(db_session, user_id)
    _take(db_session, pitch, 1, completed_at=T0, review_status="FAILED")
    current = _take(db_session, pitch, 2, status="ANALYZING")

    assert _build_previous_recurring_issues(TakeRepository(db_session), current.id, 2) == []
