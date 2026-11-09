"""모든 대상 장이 실패했지만 간이 값이 있어 200 으로 온 채점 (#1152 '모든 대상 장이 실패해도').

바꿀 코드는 없고 지금 흐름이 그대로 맞는지 남긴다: 검사 통과 · 확정본 저장 · 말하기 정확도 null ·
LLM 오류 장만 부분 재시도 1회 · 확인 없이 리뷰로 · 리뷰에는 채점 없음(null).
"""

import asyncio
import copy
import uuid

from pitch_coach_backend.module.take.ai_response import validate_coverage_reply
from pitch_coach_backend.module.take.entity import TakeSummary
from pitch_coach_backend.module.take.repository import TakeRepository
from pitch_coach_backend.module.take.service import coverage as coverage_service
from pitch_coach_backend.module.take.service.review import build_review_request
from tests.take.coverage_fixtures import FALLBACK, all_failed
from tests.test_analysis_review_ownership import in_current_session, make_ready
from tests.test_take_ai_validation import coverage


def _scoring(db, user_id):
    return make_ready(db, user_id, "SCORING")


def _stored(reply: dict) -> dict:
    return {key: value for key, value in reply.items() if key != "meta"}


def test_all_failed_reply_passes_the_check_as_received() -> None:
    take_id = uuid.uuid4()
    reply = all_failed(take_id)

    assert validate_coverage_reply(copy.deepcopy(reply), take_id) == reply


def test_all_failed_reply_is_saved_retried_once_and_reviewed_without_coverage(
    db_session, user_id, monkeypatch
) -> None:
    take = _scoring(db_session, user_id)
    reply = all_failed(take.id)
    in_current_session(monkeypatch, db_session)
    bodies = []

    async def respond(body, request_id):
        bodies.append(body)
        # 부분 재시도도 새로 성공한 장 없이 같은 간이 값을 낸다
        return copy.deepcopy(reply)

    monkeypatch.setattr(coverage_service, "request_analyze", respond)

    assert asyncio.run(coverage_service.analyze_script_coverage(take.id)) == "READY_FOR_REVIEW"

    # LLM 오류로 실패한 장만 한 번 다시 부른다
    assert [body["slide_numbers"] for body in bodies] == [None, [1]]
    repo = TakeRepository(db_session)
    saved = repo.get_ai_result(take.id, "SCRIPT_COVERAGE")
    # 새로 성공한 장이 없으니 처음 응답 그대로다 (간이 값 · Take 상세 포함)
    assert (saved.payload, saved.meta, saved.revision) == (_stored(reply), reply["meta"], 0)
    assert repo.get_ai_result(take.id, "SCRIPT_COVERAGE_PENDING") is None
    assert db_session.get(TakeSummary, take.id).speak_accuracy is None
    db_session.refresh(take)
    assert (take.analysis_stage, take.coverage_failure_code) == ("REVIEWING", None)
    assert build_review_request(db_session, take.id).script_coverage is None


def test_partial_retry_carries_the_remaining_fallback_into_the_merge(
    db_session, user_id, monkeypatch
) -> None:
    take = _scoring(db_session, user_id)
    first = all_failed(take.id)
    rubric_missing = {
        "slide_number": 2,
        "error": {"code": "RUBRIC_MISSING", "message": "평가 기준 없음"},
        "fallback": {**FALLBACK, "method": "lexical_no_rubric", "reason_codes": ["RUBRIC_MISSING"]},
    }
    first["failed"].append(rubric_missing)
    retried = coverage(take.id)
    retried["failed"] = [rubric_missing]
    merged = coverage(take.id)
    merged["failed"] = [rubric_missing]
    in_current_session(monkeypatch, db_session)
    replies = iter([first, retried])
    rescored = []

    async def analyze(body, request_id):
        return copy.deepcopy(next(replies))

    async def rescore(body, request_id):
        rescored.append(copy.deepcopy(body))
        return copy.deepcopy(merged)

    monkeypatch.setattr(coverage_service, "request_analyze", analyze)
    monkeypatch.setattr(coverage_service, "request_rescore", rescore)

    assert asyncio.run(coverage_service.analyze_script_coverage(take.id)) == "READY_FOR_REVIEW"

    # 다시 부르지 않은 장의 간이 값은 받은 그대로 rescore 로 돌려보낸다
    [body] = rescored
    assert [e["slide_number"] for e in body["evaluations"]] == [1]
    assert body["failed"] == [rubric_missing]
    saved = TakeRepository(db_session).get_ai_result(take.id, "SCRIPT_COVERAGE")
    assert (saved.payload, saved.revision) == (_stored(merged), 1)
