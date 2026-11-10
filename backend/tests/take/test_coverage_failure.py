"""대본 전달도가 확정본 없이 끝나면 마지막 실패 이유를 남기고, 확정본을 저장하면 지운다
(`takes.coverage_failure_code`)."""

import asyncio
import json

import httpx
import pytest

from pitch_coach_backend.module.take.repository import TakeRepository
from pitch_coach_backend.module.take.service import coverage as coverage_service
from tests.test_analysis_review_ownership import in_current_session, make_ready
from tests.test_take_ai_validation import ai, broken, coverage


def _answer(take_id, outcome):
    if isinstance(outcome, int):
        return httpx.Response(outcome, json={"error": {"code": "LLM_ERROR", "message": "x"}})
    if outcome == "invalid":
        return httpx.Response(200, content=json.dumps(broken(coverage(take_id), "major")))
    if outcome == "ok":
        return httpx.Response(200, json=coverage(take_id))
    raise AssertionError(outcome)


def _run(monkeypatch, db, take, *outcomes) -> tuple[str, int]:
    """AI 가 차례로 `outcomes` 로 답할 때 채점을 한 번 돌린다. (결과, 부른 횟수)."""
    in_current_session(monkeypatch, db)
    calls = []

    def handler(request):
        calls.append(request)
        outcome = outcomes[len(calls) - 1]
        if outcome == "down":
            raise httpx.ConnectError("refused", request=request)
        if outcome == "slow":
            raise httpx.ReadTimeout("timed out", request=request)
        return _answer(take.id, outcome)

    ai(monkeypatch, handler)
    result = asyncio.run(coverage_service.analyze_script_coverage(take.id))
    return result, len(calls)


@pytest.mark.parametrize(
    "outcomes,failure_code",
    [
        (("slow", "slow"), "AI_TIMEOUT"),
        ((504, 504), "AI_TIMEOUT"),
        (("down", "down"), "AI_UNAVAILABLE"),
        ((503, 503), "AI_UNAVAILABLE"),
        ((500, 502), "AI_ERROR"),
        ((429, 408), "AI_ERROR"),
        ((422,), "AI_REJECTED"),
        (("invalid",), "AI_INVALID_RESPONSE"),
        # 이유는 시도마다 덮어써 마지막 것이 남는다
        ((503, 504), "AI_TIMEOUT"),
        ((504, "invalid"), "AI_INVALID_RESPONSE"),
    ],
)
def test_analyze_without_a_result_keeps_the_last_failure(
    db_session, user_id, monkeypatch, outcomes, failure_code
) -> None:
    take = make_ready(db_session, user_id, "SCORING")

    assert _run(monkeypatch, db_session, take, *outcomes) == ("NO_COVERAGE", len(outcomes))

    db_session.refresh(take)
    assert (take.analysis_stage, take.coverage_failure_code) == ("REVIEWING", failure_code)
    assert TakeRepository(db_session).get_ai_result(take.id, "SCRIPT_COVERAGE") is None


def test_successful_retry_clears_the_failure(db_session, user_id, monkeypatch) -> None:
    take = make_ready(db_session, user_id, "SCORING")

    assert _run(monkeypatch, db_session, take, 503, "ok") == ("READY_FOR_REVIEW", 2)

    db_session.refresh(take)
    assert take.coverage_failure_code is None
    assert TakeRepository(db_session).get_ai_result(take.id, "SCRIPT_COVERAGE") is not None


def test_saving_a_result_clears_a_failure_left_by_an_earlier_job(
    db_session, user_id, monkeypatch
) -> None:
    take = make_ready(db_session, user_id, "SCORING", coverage_failure_code="AI_TIMEOUT")

    assert _run(monkeypatch, db_session, take, "ok") == ("READY_FOR_REVIEW", 1)

    db_session.refresh(take)
    assert take.coverage_failure_code is None


def test_failure_survives_a_restart_during_the_retry(db_session, user_id, monkeypatch) -> None:
    """전체 재시도를 보낸 뒤 BE 가 다시 뜨면 재시도 한도를 이미 썼으므로 다시 부르지 않는다.
    그때까지 남긴 이유로 끝난다."""
    take = make_ready(db_session, user_id, "SCORING")
    in_current_session(monkeypatch, db_session)
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(503)
        raise RuntimeError("BE 가 재시도 응답을 받기 전에 멈췄다")

    ai(monkeypatch, handler)
    with pytest.raises(RuntimeError):
        asyncio.run(coverage_service.analyze_script_coverage(take.id))

    outcome = asyncio.run(coverage_service.analyze_script_coverage(take.id, takeover=True))

    assert (outcome, calls) == ("NO_COVERAGE", 2)
    db_session.refresh(take)
    assert (take.coverage_retry_used, take.coverage_failure_code) == (True, "AI_UNAVAILABLE")
