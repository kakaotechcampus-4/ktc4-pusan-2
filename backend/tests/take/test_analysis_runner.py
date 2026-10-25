import asyncio
import copy
import json
import time
import uuid
from datetime import UTC, datetime, timedelta
from functools import partial
from unittest.mock import AsyncMock

import httpx
import pytest
from sqlalchemy.orm import Session

from pitch_coach_backend.core.tasks import TaskRegistry
from pitch_coach_backend.module import hooks
from pitch_coach_backend.module.pitch.entity import ScriptVersion
from pitch_coach_backend.module.pitch.service import script as script_service
from pitch_coach_backend.module.take.dto import ReviewRequestDTO, ReviewResponseDTO
from pitch_coach_backend.module.take.entity import Take, TakeAIResults
from pitch_coach_backend.module.take.exception import InvalidReviewResponse
from pitch_coach_backend.module.take.repository import TakeRepository
from pitch_coach_backend.module.take.service import analysis_runner
from pitch_coach_backend.module.take.service import coverage as coverage_service
from pitch_coach_backend.module.take.service import review as review_service
from tests.test_take_ai_validation import claim, coverage, release
from tests.test_take_history import _pitch, _take
from tests.test_take_review_response import REVIEW, TAKE_ID


class PlanSpy:
    def __init__(self) -> None:
        self.asked: list[uuid.UUID] = []

    def plan_next_take(self, pitch_id: uuid.UUID) -> None:
        self.asked.append(pitch_id)


@pytest.fixture(autouse=True)
def isolated_runner(monkeypatch, db_session: Session):
    monkeypatch.setattr(analysis_runner, "_running", TaskRegistry())
    monkeypatch.setattr(analysis_runner, "_recovery", TaskRegistry())
    monkeypatch.setattr(analysis_runner, "_slots_by_loop", {})

    async def in_current_session(factory, fn, *args):
        return fn(db_session, *args)

    monkeypatch.setattr(analysis_runner, "_in_session", in_current_session)
    monkeypatch.setattr(coverage_service, "_in_session", in_current_session)
    monkeypatch.setattr(review_service, "REVIEW_RETRY_DELAY_SEC", 0)


@pytest.fixture
def plans(monkeypatch) -> PlanSpy:
    spy = PlanSpy()
    monkeypatch.setattr(hooks, "_coaching", spy)
    return spy


def _analyzing(db: Session, user_id: uuid.UUID, stage: str, **fields) -> Take:
    fields.setdefault("coverage_retry_used", True)
    take = _take(db, _pitch(db, user_id), 1, status="ANALYZING", analysis_stage=stage, **fields)
    db.add(
        TakeAIResults(
            take_id=take.id, kind="TAKE_RESULT", payload={"take_id": str(take.id)}, meta={}
        )
    )
    db.commit()
    return take


def _review(take: Take) -> ReviewResponseDTO:
    body = copy.deepcopy(REVIEW)
    body.update(take_id=str(take.id), next_missions=[])
    return ReviewResponseDTO.model_validate(body)


def _states(db: Session, take: Take) -> tuple[str, str | None, str | None]:
    db.refresh(take)
    return take.status, take.review_status, take.analysis_stage


def test_scored_take_is_reviewed_and_plans_the_next_take(db_session, user_id, monkeypatch, plans):
    take = _analyzing(db_session, user_id, "SCORING")
    reviewer = AsyncMock(return_value=_review(take))
    monkeypatch.setattr(review_service, "request_review_async", reviewer)

    asyncio.run(analysis_runner.run(take.id))

    reviewer.assert_awaited_once()
    assert _states(db_session, take) == ("COMPLETED", "DONE", "DONE")
    assert TakeRepository(db_session).get_ai_result(take.id, "REVIEW") is not None
    assert plans.asked == [take.pitch_id]


def test_review_failure_completes_the_take_and_still_plans(
    db_session, user_id, monkeypatch, plans
):
    take = _analyzing(db_session, user_id, "REVIEWING")
    monkeypatch.setattr(
        review_service,
        "request_review_async",
        AsyncMock(side_effect=InvalidReviewResponse()),
    )

    asyncio.run(analysis_runner.run(take.id))

    assert _states(db_session, take) == ("COMPLETED", "FAILED", "DONE")
    assert TakeRepository(db_session).get_ai_result(take.id, "REVIEW") is None
    assert plans.asked == [take.pitch_id]


@pytest.mark.parametrize("stage", ["FINALIZING", "DONE"])
def test_take_not_ready_for_review_is_left_alone(db_session, user_id, monkeypatch, plans, stage):
    take = _analyzing(db_session, user_id, stage)
    reviewer = AsyncMock(side_effect=AssertionError("리뷰를 부르면 안 된다"))
    monkeypatch.setattr(review_service, "request_review_async", reviewer)

    asyncio.run(analysis_runner.run(take.id))

    reviewer.assert_not_awaited()
    assert _states(db_session, take) == ("ANALYZING", None, stage)
    assert plans.asked == []


def test_held_take_is_not_reviewed(db_session, user_id, monkeypatch, plans):
    take = _analyzing(
        db_session, user_id, "SCORING", analysis_hold_reason="COVERAGE_RESCORE_FAILED"
    )
    reviewer = AsyncMock(side_effect=AssertionError("리뷰를 부르면 안 된다"))
    monkeypatch.setattr(review_service, "request_review_async", reviewer)

    asyncio.run(analysis_runner.run(take.id))

    reviewer.assert_not_awaited()
    assert _states(db_session, take) == ("ANALYZING", None, "SCORING")
    assert plans.asked == []


def test_confirmation_waits_until_its_deadline(db_session, user_id, monkeypatch, plans):
    take = _analyzing(db_session, user_id, "AWAITING_CONFIRMATION")
    take.analysis_stage_at = (
        datetime.now(UTC) - analysis_runner.CONFIRMATION_WAIT + timedelta(seconds=0.3)
    )
    db_session.commit()
    monkeypatch.setattr(
        review_service, "request_review_async", AsyncMock(return_value=_review(take))
    )

    started = time.monotonic()
    asyncio.run(analysis_runner.run(take.id))

    assert time.monotonic() - started >= 0.2
    assert _states(db_session, take) == ("COMPLETED", "DONE", "DONE")


def test_expired_confirmation_is_reviewed_at_once(db_session, user_id, monkeypatch, plans):
    take = _analyzing(db_session, user_id, "AWAITING_CONFIRMATION")
    take.analysis_stage_at = datetime.now(UTC) - timedelta(minutes=30)
    db_session.commit()
    monkeypatch.setattr(
        review_service, "request_review_async", AsyncMock(return_value=_review(take))
    )

    started = time.monotonic()
    asyncio.run(analysis_runner.run(take.id))

    assert time.monotonic() - started < 1
    assert _states(db_session, take) == ("COMPLETED", "DONE", "DONE")


def _rubric_pending(db: Session, take: Take, started_ago: timedelta) -> None:
    db.get(ScriptVersion, take.script_version_id).rubric_status = "PENDING"
    take.analysis_stage_at = datetime.now(UTC) - started_ago
    db.commit()


def test_scoring_waits_while_the_rubric_is_pending(db_session, user_id, monkeypatch):
    take = _analyzing(db_session, user_id, "SCORING", coverage_retry_used=False)
    _rubric_pending(db_session, take, timedelta(seconds=10))
    analyze = AsyncMock(side_effect=AssertionError("기준이 끝나기 전에 채점하면 안 된다"))
    monkeypatch.setattr(coverage_service, "request_analyze", analyze)

    outcome = asyncio.run(coverage_service.analyze_script_coverage(take.id))

    assert outcome == "RUBRIC_PENDING"
    analyze.assert_not_awaited()
    assert _states(db_session, take) == ("ANALYZING", None, "SCORING")


def test_scoring_goes_on_once_the_rubric_wait_times_out(db_session, user_id):
    take = _analyzing(db_session, user_id, "SCORING")
    _rubric_pending(db_session, take, coverage_service.RUBRIC_WAIT + timedelta(seconds=1))

    assert asyncio.run(coverage_service.analyze_script_coverage(take.id)) == "NO_COVERAGE"


def test_retry_of_a_scored_take_does_not_wait_for_the_rubric(db_session, user_id):
    take = _analyzing(db_session, user_id, "SCORING")
    _rubric_pending(db_session, take, timedelta(seconds=10))
    requested_at = claim(db_session, take.id)
    saved = coverage_service._save_confirmed(db_session, take.id, requested_at, coverage(take.id))
    assert saved is not None
    release(db_session, take.id)

    assert asyncio.run(coverage_service.analyze_script_coverage(take.id)) == "READY_FOR_REVIEW"


def test_runner_polls_until_the_rubric_is_ready(db_session, user_id, monkeypatch, plans):
    take = _analyzing(db_session, user_id, "SCORING")
    _rubric_pending(db_session, take, timedelta(seconds=10))
    monkeypatch.setattr(analysis_runner, "RUBRIC_POLL_SEC", 0)
    checks: list[bool] = []

    def pending(db, script_version_id):
        checks.append(len(checks) < 2)
        return checks[-1]

    monkeypatch.setattr(script_service, "rubric_pending", pending)
    monkeypatch.setattr(
        review_service, "request_review_async", AsyncMock(return_value=_review(take))
    )

    asyncio.run(analysis_runner.run(take.id))

    assert checks == [True, True, False]
    assert _states(db_session, take) == ("COMPLETED", "DONE", "DONE")


def test_same_take_runs_once(monkeypatch):
    gate = asyncio.Event()
    runs: list[uuid.UUID] = []

    async def blocked(take_id, session_factory, restarted):
        runs.append(take_id)
        await gate.wait()

    monkeypatch.setattr(analysis_runner, "_run", blocked)

    async def scenario():
        first = analysis_runner.start(TAKE_ID)
        second = analysis_runner.start(TAKE_ID)
        await asyncio.sleep(0)
        gate.set()
        await first
        return first, second

    first, second = asyncio.run(scenario())
    assert first is not None
    assert second is None
    assert runs == [TAKE_ID]


def test_analyzing_takes_lists_resumable_stages_only(db_session, user_id):
    pitch = _pitch(db_session, user_id)
    old = datetime.now(UTC) - timedelta(hours=1)
    expected = [
        _take(db_session, pitch, number, status="ANALYZING", analysis_stage=stage,
              analysis_stage_at=old).id
        for number, stage in enumerate(["SCORING", "AWAITING_CONFIRMATION", "REVIEWING"], 1)
    ]
    _take(db_session, pitch, 4, status="ANALYZING", analysis_stage="FINALIZING")
    _take(db_session, pitch, 5, status="COMPLETED", analysis_stage="DONE")
    _take(db_session, pitch, 6, status="ANALYZING", analysis_stage="SCORING",
          analysis_hold_reason="COVERAGE_RESCORE_FAILED")
    fresh = _take(db_session, pitch, 7, status="ANALYZING", analysis_stage="REVIEWING",
                  analysis_stage_at=datetime.now(UTC)).id
    db_session.commit()
    repo = TakeRepository(db_session)

    assert set(repo.analyzing_takes()) == {*expected, fresh}
    assert set(repo.analyzing_takes(datetime.now(UTC) - timedelta(minutes=10))) == set(expected)


def test_resume_starts_every_resumable_take(monkeypatch, db_session, user_id):
    pitch = _pitch(db_session, user_id)
    take_ids = [
        _take(db_session, pitch, number, status="ANALYZING", analysis_stage="REVIEWING").id
        for number in range(1, 7)
    ]
    db_session.commit()
    started: list[tuple[uuid.UUID, bool]] = []

    async def record(take_id, session_factory, restarted):
        started.append((take_id, restarted))

    monkeypatch.setattr(analysis_runner, "_run", record)

    async def scenario():
        analysis_runner.resume(restarted=True)
        await asyncio.gather(*analysis_runner._recovery.tasks())
        await asyncio.gather(*analysis_runner._running.tasks())

    asyncio.run(scenario())
    assert sorted(started) == sorted((take_id, True) for take_id in take_ids)


def test_sweep_does_not_take_over_running_reviews(monkeypatch):
    calls: list[tuple[datetime | None, bool]] = []

    def record(session_factory=None, stale_before=None, *, restarted=False):
        calls.append((stale_before, restarted))
        raise asyncio.CancelledError

    monkeypatch.setattr(analysis_runner, "resume", record)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(analysis_runner.sweep_forever(interval_sec=0))

    ((stale_before, restarted),) = calls
    assert stale_before is not None
    assert restarted is False


def _interrupted_review(db: Session, user_id: uuid.UUID) -> Take:
    take = _analyzing(db, user_id, "REVIEWING")
    take.review_request_id = uuid.uuid4()
    take.analysis_stage_at = datetime.now(UTC) - timedelta(seconds=30)
    db.commit()
    return take


def test_fresh_review_is_not_taken_over_without_a_restart(db_session, user_id, monkeypatch, plans):
    take = _interrupted_review(db_session, user_id)
    owner = take.review_request_id
    reviewer = AsyncMock(side_effect=AssertionError("돌고 있는 리뷰를 가로채면 안 된다"))
    monkeypatch.setattr(review_service, "request_review_async", reviewer)

    asyncio.run(analysis_runner.run(take.id))

    reviewer.assert_not_awaited()
    db_session.refresh(take)
    assert take.review_request_id == owner
    assert plans.asked == []


def test_restart_takes_over_a_review_cut_off_by_the_restart(
    db_session, user_id, monkeypatch, plans
):
    take = _interrupted_review(db_session, user_id)
    owner = take.review_request_id
    monkeypatch.setattr(
        review_service, "request_review_async", AsyncMock(return_value=_review(take))
    )

    asyncio.run(analysis_runner.run(take.id, restarted=True))

    assert _states(db_session, take) == ("COMPLETED", "DONE", "DONE")
    assert take.review_request_id != owner
    assert plans.asked == [take.pitch_id]


def _ai(monkeypatch, handler) -> list[httpx.Request]:
    calls: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return handler(request, len(calls))

    client = partial(httpx.AsyncClient, transport=httpx.MockTransport(record))
    monkeypatch.setattr(review_service.httpx, "AsyncClient", client)
    return calls


def test_async_review_retries_a_transient_status_once(monkeypatch):
    reply = copy.deepcopy(REVIEW)
    calls = _ai(
        monkeypatch,
        lambda request, n: httpx.Response(503)
        if n == 1
        else httpx.Response(200, content=json.dumps(reply).encode()),
    )

    review = asyncio.run(
        review_service.request_review_async(TAKE_ID, ReviewRequestDTO(take_result={}))
    )

    assert review.take_id == TAKE_ID
    assert len(calls) == 2
    assert calls[0].headers["X-Request-ID"] == calls[1].headers["X-Request-ID"]


@pytest.mark.parametrize("status,attempts", [(422, 1), (404, 1), (429, 2), (500, 2)])
def test_async_review_errors_become_invalid_review(monkeypatch, status, attempts):
    calls = _ai(monkeypatch, lambda request, n: httpx.Response(status))

    with pytest.raises(InvalidReviewResponse):
        asyncio.run(
            review_service.request_review_async(TAKE_ID, ReviewRequestDTO(take_result={}))
        )

    assert len(calls) == attempts


@pytest.mark.parametrize("restarted", [False, True], ids=["running", "restarted"])
def test_runner_takes_over_scoring_only_after_a_restart(monkeypatch, restarted):
    seen = []

    async def analyze(take_id, session_factory, *, takeover):
        seen.append(takeover)
        return "HELD"

    monkeypatch.setattr(coverage_service, "analyze_script_coverage", analyze)

    asyncio.run(analysis_runner.run(uuid.uuid4(), restarted=restarted))

    assert seen == [restarted]
