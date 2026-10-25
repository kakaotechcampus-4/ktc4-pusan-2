"""리뷰의 잘못된 응답과 전송 실패는 저장하지 않고 도메인 502로 처리한다."""

import asyncio
import copy
import json
import time
import uuid
from functools import partial

import httpx
import pytest

from pitch_coach_backend.module.take.dto import (
    ReviewRecurringIssueDTO,
    ReviewResponseDTO,
)
from pitch_coach_backend.module.take.entity import Mission, TakeAIResults, TakeSummary
from pitch_coach_backend.module.take.exception import InvalidReviewResponse
from pitch_coach_backend.module.take.repository import TakeRepository
from pitch_coach_backend.module.take.service import review as review_service
from tests.test_analysis_review_ownership import run_review
from tests.test_take_history import _pitch, _take
from tests.test_take_review_response import REVIEW, TAKE_ID, request_review


@pytest.fixture(autouse=True)
def no_retry_delay(monkeypatch):
    monkeypatch.setattr(review_service, "REVIEW_RETRY_DELAY_SEC", 0)


def ai(monkeypatch, handler) -> None:
    client = partial(httpx.AsyncClient, transport=httpx.MockTransport(handler))
    monkeypatch.setattr(review_service.httpx, "AsyncClient", client)


@pytest.mark.parametrize("case", ["major", "nul", "nan", "surrogate", "score-overflow"])
def test_invalid_review_does_not_retry(monkeypatch, case) -> None:
    reply = copy.deepcopy(REVIEW)
    if case == "major":
        reply["meta"]["schema_version"] = "99.0"
    elif case == "nul":
        reply["overall_summary"] = "bad\x00text"
    elif case == "nan":
        reply["future_field"] = float("nan")
    elif case == "surrogate":
        reply["overall_summary"] = "\ud800"
    else:
        reply["score"] = 2**40
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, content=json.dumps(reply).encode())

    ai(monkeypatch, handler)
    with pytest.raises(InvalidReviewResponse):
        request_review()
    assert len(calls) == 1


@pytest.mark.parametrize(
    "status,attempts", [(422, 1), (404, 1), (302, 1), (408, 2), (429, 2), (500, 2), (503, 2)]
)
def test_review_http_errors_are_502_and_retry_only_transient_statuses(
    monkeypatch,
    status,
    attempts,
) -> None:
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(status)

    ai(monkeypatch, handler)
    with pytest.raises(InvalidReviewResponse) as error:
        request_review()
    assert error.value.status_code == 502
    assert len(calls) == attempts
    assert len({request.headers["X-Request-ID"] for request in calls}) == 1


@pytest.mark.parametrize("error_type", [httpx.ConnectError, httpx.ReadTimeout])
def test_review_network_failure_becomes_502_after_one_retry(monkeypatch, error_type) -> None:
    calls = []

    def handler(request):
        calls.append(request)
        raise error_type("fake network failure", request=request)

    ai(monkeypatch, handler)
    with pytest.raises(InvalidReviewResponse):
        request_review()
    assert len(calls) == 2
    assert calls[0].headers["X-Request-ID"] == calls[1].headers["X-Request-ID"]


async def _trickle(body: bytes):
    for start in range(0, len(body), 40):
        await asyncio.sleep(0.02)
        yield body[start : start + 40]


@pytest.mark.parametrize("slow", ["trickled-body", "late-response"])
def test_review_attempt_is_limited_as_a_whole(monkeypatch, slow) -> None:
    """읽기 간격이 짧아도 시도 하나가 전체 제한을 넘으면 버리고 한 번만 다시 부른다."""
    monkeypatch.setattr(review_service, "REVIEW_TIMEOUT_SEC", 0.1)
    body = json.dumps(REVIEW).encode()
    calls = []

    async def handler(request):
        calls.append(request)
        if slow == "late-response":
            await asyncio.sleep(1)
            return httpx.Response(200, content=body)
        return httpx.Response(200, content=_trickle(body))

    ai(monkeypatch, handler)
    started = time.monotonic()
    with pytest.raises(InvalidReviewResponse):
        request_review()
    assert time.monotonic() - started < 0.8
    assert len(calls) == 2
    assert calls[0].headers["X-Request-ID"] == calls[1].headers["X-Request-ID"]


def test_review_retry_after_an_attempt_runs_out_of_time(monkeypatch) -> None:
    monkeypatch.setattr(review_service, "REVIEW_TIMEOUT_SEC", 0.1)
    body = json.dumps(REVIEW).encode()
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, content=_trickle(body) if len(calls) == 1 else body)

    ai(monkeypatch, handler)
    assert request_review().take_id == TAKE_ID
    assert len(calls) == 2
    assert calls[0].headers["X-Request-ID"] == calls[1].headers["X-Request-ID"]


def test_review_retry_succeeds_and_fallback_is_a_normal_result(monkeypatch) -> None:
    reply = copy.deepcopy(REVIEW)
    reply["meta"]["schema_version"] = "1.9"
    reply["fallback_reason"] = "LLM_UNAVAILABLE"
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(503) if len(calls) == 1 else httpx.Response(200, json=reply)

    ai(monkeypatch, handler)
    assert request_review().fallback_reason
    assert len(calls) == 2
    assert calls[0].headers["X-Request-ID"] == calls[1].headers["X-Request-ID"]


@pytest.mark.parametrize(
    "case", ["other-take", "major", "nul", "recurring-too-many", "recurring-duplicate"]
)
def test_review_storage_validates_again_before_any_write(db_session, user_id, case) -> None:
    take = _take(
        db_session, _pitch(db_session, user_id), 1, status="ANALYZING", analysis_stage="REVIEWING"
    )
    review = ReviewResponseDTO.model_validate({**REVIEW, "take_id": str(take.id)})
    if case == "other-take":
        review.take_id = uuid.uuid4()
    elif case == "major":
        review.meta.schema_version = "99.0"
    elif case == "nul":
        review.overall_summary = "bad\x00text"
    else:
        # 모델을 만든 뒤 목록을 고치면 검사를 거치지 않는다. 저장 직전 재검사가 막아야 한다
        issue = ReviewRecurringIssueDTO(area="GAZE", dimension="audience_gaze")
        review.recurring_issues = [issue] * (21 if case == "recurring-too-many" else 2)

    with pytest.raises(InvalidReviewResponse):
        review_service.save_review(db_session, take.id, review)

    assert TakeRepository(db_session).get_ai_result(take.id, "REVIEW") is None
    assert db_session.query(Mission).filter_by(source_take_id=take.id).count() == 0
    assert db_session.query(TakeSummary).filter_by(take_id=take.id).count() == 0


def test_review_failure_completes_the_take_and_keeps_measurements(
    db_session,
    user_id,
    monkeypatch,
) -> None:
    pitch = _pitch(db_session, user_id)
    take = _take(db_session, pitch, 1, status="ANALYZING", analysis_stage="AWAITING_CONFIRMATION")
    db_session.add(
        TakeAIResults(
            take_id=take.id, kind="TAKE_RESULT", payload={"take_id": str(take.id)}, meta={}
        )
    )
    db_session.flush()
    ai(monkeypatch, lambda request: httpx.Response(503))

    assert run_review(monkeypatch, db_session, take.id) == pitch.id

    db_session.refresh(take)
    assert (take.status, take.review_status, take.analysis_stage) == ("COMPLETED", "FAILED", "DONE")
    repo = TakeRepository(db_session)
    assert repo.get_ai_result(take.id, "REVIEW") is None
    assert repo.get_ai_result(take.id, "TAKE_RESULT") is not None
