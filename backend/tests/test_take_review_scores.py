"""이전 Take의 대본 채점 객체를 리뷰 요청에 그대로 싣는다."""

import uuid

import pytest
from pydantic import ValidationError
from sqlalchemy.orm import Session

from pitch_coach_backend.module.take.dto import PreviousTakeDTO
from pitch_coach_backend.module.take.entity import TakeAIResults
from pitch_coach_backend.module.take.service import build_review_request
from tests.test_take_history import _pitch, _take


@pytest.mark.parametrize(
    "coverage",
    [
        {"take_scores": {
            "content_coverage": 0.81, "critical_fact_accuracy": None,
            "script_fidelity": 0.74, "similar_words": 1,
            "similar_numbers": 0, "scored_ratio": 0.8,
        }},
        {"take_scores": {
            "content_coverage": None, "critical_fact_accuracy": None,
            "script_fidelity": None, "similar_words": 0,
            "similar_numbers": 0, "scored_ratio": 0.0,
        }},
        {"take_scores": None},
        {},
        None,
    ],
    ids=["scores-object", "unmeasurable", "null-scores", "missing-scores", "no-coverage"],
)
def test_review_request_serializes_previous_saved_scores(
    db_session: Session, user_id: uuid.UUID, coverage: dict | None,
) -> None:
    pitch = _pitch(db_session, user_id)
    previous = _take(db_session, pitch, 1)
    current = _take(db_session, pitch, 2, status="ANALYZING")
    for take in (previous, current):
        db_session.add(TakeAIResults(
            take_id=take.id, kind="TAKE_RESULT", payload={"take_id": str(take.id)}, meta={},
        ))
    if coverage is not None:
        db_session.add(TakeAIResults(
            take_id=previous.id, kind="SCRIPT_COVERAGE", payload=coverage, meta={},
        ))
    db_session.flush()

    request = build_review_request(db_session, current.id).model_dump(mode="json")

    assert request["previous_takes"] == [{
        "take_result": {"take_id": str(previous.id)},
        "take_scores": coverage.get("take_scores") if coverage else None,
    }]


def test_previous_scores_reject_an_array() -> None:
    with pytest.raises(ValidationError):
        PreviousTakeDTO(take_result={}, take_scores=[81, 74])
