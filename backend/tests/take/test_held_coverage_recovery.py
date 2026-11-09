"""리뷰를 보류한 Take 를 사람이 정해 복구한다 (`docs/ai/architecture.md` 1-3 '리뷰 보류' · 1-7).

보존한 후보와 고정 rubric 으로 병합 rescore 를 한 번 더 부르고, 성공하면 보류를 풀고 확인 · 리뷰로
이어 간다. 재시도 기록 칸은 되돌리지 않는다.
"""

import asyncio
import copy
import uuid
from datetime import timedelta
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import delete, func, update
from sqlalchemy.orm import Session

from pitch_coach_backend.core.tasks import TaskRegistry
from pitch_coach_backend.module import hooks
from pitch_coach_backend.module.pitch.entity import ScriptSlide
from pitch_coach_backend.module.take.adapter import ai_client as take_ai_client
from pitch_coach_backend.module.take.ai_response import CoverageCallError
from pitch_coach_backend.module.take.dto import ReviewResponseDTO
from pitch_coach_backend.module.take.entity import Take, TakeAIResults, TakeSummary
from pitch_coach_backend.module.take.repository import COVERAGE_STALE_AFTER, TakeRepository
from pitch_coach_backend.module.take.service import analysis_runner
from pitch_coach_backend.module.take.service import coverage as coverage_service
from tests.test_take_ai_validation import claim, coverage
from tests.test_take_history import _pitch, _take
from tests.test_take_review_response import REVIEW

RUBRIC = {"rubric_id": "rubric-1", "slide_number": 1}
FAILED_SLIDE = {"slide_number": 2, "error": {"code": "LLM_ERROR", "message": "합성 실패"}}


class PlanSpy:
    def __init__(self) -> None:
        self.asked: list[uuid.UUID] = []

    def plan_next_take(self, pitch_id: uuid.UUID) -> None:
        self.asked.append(pitch_id)


@pytest.fixture(autouse=True)
def current_session(monkeypatch, db_session: Session):
    monkeypatch.setattr(analysis_runner, "_running", TaskRegistry())

    async def run(factory, fn, *args):
        return fn(db_session, *args)

    monkeypatch.setattr(analysis_runner, "_in_session", run)
    monkeypatch.setattr(coverage_service, "_in_session", run)


@pytest.fixture
def plans(monkeypatch) -> PlanSpy:
    spy = PlanSpy()
    monkeypatch.setattr(hooks, "_coaching", spy)
    return spy


def _held(db: Session, user_id: uuid.UUID, *, rescore_retry_used: bool = True) -> Take:
    """병합 rescore 가 끝내 실패해 리뷰를 보류한 Take. 확정본 · 후보가 남고 표식은 놓았다."""
    take = _take(db, _pitch(db, user_id), 1, status="ANALYZING", analysis_stage="SCORING")
    db.add_all(
        [
            ScriptSlide(
                script_version_id=take.script_version_id,
                slide_number=1,
                full_content="합성 대본",
                rubric=RUBRIC,
            ),
            TakeAIResults(
                take_id=take.id, kind="TAKE_RESULT", payload={"take_id": str(take.id)}, meta={}
            ),
        ]
    )
    db.flush()
    reply = coverage(take.id)
    requested_at = claim(db, take.id)
    assert coverage_service._save_confirmed(db, take.id, requested_at, reply) is not None
    assert coverage_service._save_candidate(
        db,
        take.id,
        requested_at,
        {
            "script_version_id": str(take.script_version_id),
            "base_revision": 0,
            "evaluations": reply["evaluations"],
            "failed": [FAILED_SLIDE],
            "retry_meta": reply["meta"],
        },
    )
    coverage_service._hold_coverage(db, take.id, requested_at)
    assert coverage_service._conclude_coverage(db, take.id, requested_at) == "HELD"
    take.coverage_rescore_retry_used = rescore_retry_used
    db.commit()
    return take


def _merged(take: Take) -> dict:
    reply = coverage(take.id)
    reply["take_scores"]["content_coverage"] = 0.9
    return reply


def test_recovery_rescore_lifts_the_hold_and_keeps_the_retry_record(
    db_session, user_id, monkeypatch
):
    take = _held(db_session, user_id)
    rescore = AsyncMock(return_value=_merged(take))
    monkeypatch.setattr(coverage_service, "request_rescore", rescore)

    outcome = asyncio.run(coverage_service.recover_held_coverage(take.id))

    assert outcome == "READY_FOR_REVIEW"
    rescore.assert_awaited_once()
    body = rescore.await_args.args[0]
    assert body == {
        "take_id": str(take.id),
        "evaluations": coverage(take.id)["evaluations"],
        "rubrics": [RUBRIC],
        "answers": [],
        "failed": [FAILED_SLIDE],
    }
    db_session.refresh(take)
    assert (take.analysis_stage, take.analysis_hold_reason, take.coverage_requested_at) == (
        "REVIEWING",
        None,
        None,
    )
    # 재시도 기록 칸은 되돌리지 않는다 (architecture.md 1-7)
    assert take.coverage_rescore_retry_used is True
    repo = TakeRepository(db_session)
    assert repo.get_ai_result(take.id, "SCRIPT_COVERAGE").revision == 1
    assert repo.get_ai_result(take.id, "SCRIPT_COVERAGE_PENDING") is None
    assert db_session.query(TakeSummary).filter_by(take_id=take.id).one().speak_accuracy == 90


@pytest.mark.parametrize("retryable", [True, False])
def test_failed_recovery_rescore_keeps_the_hold(db_session, user_id, monkeypatch, retryable):
    take = _held(db_session, user_id, rescore_retry_used=False)
    rescore = AsyncMock(side_effect=CoverageCallError("합성 실패", retryable=retryable))
    monkeypatch.setattr(coverage_service, "request_rescore", rescore)

    outcome = asyncio.run(coverage_service.recover_held_coverage(take.id))

    assert outcome == "HELD"
    # 누를 때마다 한 번만 부른다. 일시 오류여도 이 경로에서 재시도를 더하지 않는다
    rescore.assert_awaited_once()
    db_session.refresh(take)
    assert (take.analysis_stage, take.analysis_hold_reason, take.coverage_requested_at) == (
        "SCORING",
        "COVERAGE_RESCORE_FAILED",
        None,
    )
    assert take.coverage_rescore_retry_used is False
    repo = TakeRepository(db_session)
    assert repo.get_ai_result(take.id, "SCRIPT_COVERAGE").revision == 0
    assert repo.get_ai_result(take.id, "SCRIPT_COVERAGE_PENDING") is not None
    assert db_session.query(TakeSummary).filter_by(take_id=take.id).one().speak_accuracy == 81


def test_recovery_leaves_a_take_that_is_not_held_alone(db_session, user_id, monkeypatch):
    take = _take(
        db_session, _pitch(db_session, user_id), 1, status="ANALYZING", analysis_stage="SCORING"
    )
    db_session.commit()
    rescore = AsyncMock(side_effect=AssertionError("부르면 안 된다"))
    monkeypatch.setattr(coverage_service, "request_rescore", rescore)

    assert asyncio.run(coverage_service.recover_held_coverage(take.id)) == "SKIPPED"

    rescore.assert_not_awaited()
    db_session.refresh(take)
    assert take.coverage_requested_at is None


def test_held_recovery_says_whether_the_hold_can_be_lifted(db_session, user_id):
    take = _held(db_session, user_id)
    repo = TakeRepository(db_session)

    assert coverage_service.held_recovery(db_session, uuid.uuid4()) is None
    assert coverage_service.held_recovery(db_session, take.id) == "READY"

    # 앞서 누른 복구가 표식을 쥐고 있다
    assert repo.claim_coverage(take.id, held=True) is not None
    db_session.commit()
    assert coverage_service.held_recovery(db_session, take.id) == "BUSY"

    # 그 작업이 끊겨 표식이 낡았다
    db_session.execute(
        update(Take)
        .where(Take.id == take.id)
        .values(coverage_requested_at=func.now() - COVERAGE_STALE_AFTER - timedelta(seconds=1))
    )
    db_session.commit()
    assert coverage_service.held_recovery(db_session, take.id) == "READY"

    db_session.execute(
        delete(TakeAIResults).where(
            TakeAIResults.take_id == take.id, TakeAIResults.kind == "SCRIPT_COVERAGE_PENDING"
        )
    )
    db_session.commit()
    assert coverage_service.held_recovery(db_session, take.id) == "NOT_HELD"


def test_take_that_is_not_held_cannot_be_recovered(db_session, user_id):
    take = _take(
        db_session, _pitch(db_session, user_id), 1, status="ANALYZING", analysis_stage="SCORING"
    )
    db_session.commit()

    assert coverage_service.held_recovery(db_session, take.id) == "NOT_HELD"
    assert TakeRepository(db_session).claim_coverage(take.id, held=True) is None


def _review(take: Take) -> ReviewResponseDTO:
    body = copy.deepcopy(REVIEW)
    body.update(take_id=str(take.id), next_missions=[])
    return ReviewResponseDTO.model_validate(body)


def test_lifted_hold_goes_on_to_review_and_plans_the_next_take(
    db_session, user_id, monkeypatch, plans
):
    take = _held(db_session, user_id)
    monkeypatch.setattr(coverage_service, "request_rescore", AsyncMock(return_value=_merged(take)))
    reviewer = AsyncMock(return_value=_review(take))
    monkeypatch.setattr(take_ai_client, "request_review_async", reviewer)

    async def scenario():
        task = analysis_runner.recover_held(take.id)
        assert task is not None
        await task

    asyncio.run(scenario())

    reviewer.assert_awaited_once()
    db_session.refresh(take)
    assert (take.status, take.review_status, take.analysis_stage) == ("COMPLETED", "DONE", "DONE")
    assert take.analysis_hold_reason is None
    assert plans.asked == [take.pitch_id]


def test_hold_that_stays_is_not_reviewed(db_session, user_id, monkeypatch, plans):
    take = _held(db_session, user_id)
    monkeypatch.setattr(
        coverage_service,
        "request_rescore",
        AsyncMock(side_effect=CoverageCallError("합성 실패", retryable=True)),
    )
    reviewer = AsyncMock(side_effect=AssertionError("리뷰를 부르면 안 된다"))
    monkeypatch.setattr(take_ai_client, "request_review_async", reviewer)

    async def scenario():
        await analysis_runner.recover_held(take.id)

    asyncio.run(scenario())

    reviewer.assert_not_awaited()
    db_session.refresh(take)
    assert (take.status, take.analysis_hold_reason) == ("ANALYZING", "COVERAGE_RESCORE_FAILED")
    assert plans.asked == []


def test_recovery_is_not_started_while_the_take_is_analyzed(monkeypatch):
    gate = asyncio.Event()

    async def blocked(take_id, session_factory, restarted):
        await gate.wait()

    monkeypatch.setattr(analysis_runner, "_run", blocked)
    take_id = uuid.uuid4()

    async def scenario():
        running = analysis_runner.start(take_id)
        recovery = analysis_runner.recover_held(take_id)
        gate.set()
        await running
        return recovery

    assert asyncio.run(scenario()) is None
