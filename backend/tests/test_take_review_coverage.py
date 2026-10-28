"""리뷰에는 성공한 채점을 싣고 전체 실패 진단은 저장본에 보존한다."""

import uuid

import pytest
from sqlalchemy.orm import Session

from pitch_coach_backend.module.take.entity import TakeAIResults, TakeSummary
from pitch_coach_backend.module.take.repository import TakeRepository
from pitch_coach_backend.module.take.service import coverage as coverage_service
from pitch_coach_backend.module.take.service.review import build_review_request
from tests.test_take_ai_validation import claim, coverage
from tests.test_take_history import _pitch, _take


@pytest.mark.parametrize(
    "case", ["all-failed", "partially-scored", "no-coverage", "zero-score", "all-scored"]
)
def test_review_coverage_depends_on_successful_evaluations(
    db_session: Session, user_id: uuid.UUID, case: str
) -> None:
    take = _take(
        db_session, _pitch(db_session, user_id), 1, status="ANALYZING", analysis_stage="SCORING"
    )
    result = {"take_id": str(take.id)}
    db_session.add(TakeAIResults(take_id=take.id, kind="TAKE_RESULT", payload=result, meta={}))
    db_session.commit()

    payload = None
    reply = coverage(take.id)
    if case in ("all-failed", "partially-scored"):
        reply["failed"] = [
            {
                "slide_number": 1 if case == "all-failed" else 2,
                "error": {"code": "RUBRIC_MISSING", "message": "평가 기준 없음"},
            }
        ]
        reply["take_scores"]["scored_ratio"] = 0.0 if case == "all-failed" else 0.5
    if case == "all-failed":
        reply["evaluations"] = []
        reply["take_scores"].update(
            content_coverage=None,
            critical_fact_accuracy=None,
            script_fidelity=None,
            similar_words=0,
            similar_numbers=0,
        )
    elif case == "zero-score":
        reply["evaluations"][0]["scores"]["content_coverage"] = 0.0
        reply["take_scores"]["content_coverage"] = 0.0
    requested_at = claim(db_session, take.id)
    if case != "no-coverage":
        payload = {key: value for key, value in reply.items() if key != "meta"}
        assert coverage_service._save_confirmed(db_session, take.id, requested_at, reply) == payload

    expected_stage = "NO_COVERAGE" if case == "no-coverage" else "READY_FOR_REVIEW"
    assert coverage_service._conclude_coverage(db_session, take.id, requested_at) == expected_stage

    request = build_review_request(db_session, take.id).model_dump(mode="json")

    assert request["take_result"] == result
    assert request["script_coverage"] == (None if case == "all-failed" else payload)
    db_session.expire_all()
    assert take.analysis_stage == "REVIEWING"
    saved = TakeRepository(db_session).get_ai_result(take.id, "SCRIPT_COVERAGE")
    if case == "no-coverage":
        assert saved is None
    else:
        assert saved is not None
        assert saved.payload == payload
        assert saved.meta == reply["meta"]
        assert saved.revision == 0
        if case == "all-failed":
            assert db_session.get(TakeSummary, take.id).speak_accuracy is None
