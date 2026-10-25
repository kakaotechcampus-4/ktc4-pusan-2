"""채점 뒤 리뷰 실행과 오래된 응답·재시도 한도를 검사한다."""

import asyncio
import copy
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import delete, update

from pitch_coach_backend.module.take.dto import ReviewResponseDTO
from pitch_coach_backend.module.take.entity import Mission, Take, TakeAIResults, TakeSummary
from pitch_coach_backend.module.take.repository import TakeRepository
from pitch_coach_backend.module.take.service import analysis_runner
from pitch_coach_backend.module.take.service import coverage as coverage_service
from pitch_coach_backend.module.take.service import review as review_service
from tests.test_take_ai_validation import claim, coverage
from tests.test_take_history import _pitch, _take
from tests.test_take_review_response import REVIEW


def review(take, *, summary="새 리뷰", score=90):
    body = copy.deepcopy(REVIEW)
    body.update(take_id=str(take.id), overall_summary=summary, score=score, next_missions=[])
    return ReviewResponseDTO.model_validate(body)


def make_ready(db, user_id, stage="AWAITING_CONFIRMATION", **fields):
    take = _take(db, _pitch(db, user_id), 1, status="ANALYZING", analysis_stage=stage, **fields)
    db.add(
        TakeAIResults(
            take_id=take.id, kind="TAKE_RESULT", payload={"take_id": str(take.id)}, meta={}
        )
    )
    db.commit()
    return take


def in_current_session(monkeypatch, db):
    async def run(factory, fn, *args):
        return fn(db, *args)

    monkeypatch.setattr(coverage_service, "_in_session", run)


def run_review(monkeypatch, db, take_id):
    """앱 경로(analysis_runner._review)로 리뷰를 한 번 돌린다. 실제로는 호출마다 세션을 새로 열고
    닫으므로 예외로 끝난 호출의 변경은 버린다."""

    async def run(factory, fn, *args):
        try:
            return fn(db, *args)
        except Exception:
            db.rollback()
            raise

    monkeypatch.setattr(analysis_runner, "_in_session", run)
    monkeypatch.setattr(analysis_runner, "_slots_by_loop", {})
    return asyncio.run(analysis_runner._review(take_id, None, False))


def test_scored_take_starts_review_immediately(db_session, user_id, monkeypatch):
    take = make_ready(db_session, user_id, "SCORING")
    requested_at = claim(db_session, take.id)
    saved = coverage_service._save_confirmed(db_session, take.id, requested_at, coverage(take.id))
    assert saved is not None
    assert coverage_service._conclude_coverage(db_session, take.id, requested_at) == (
        "READY_FOR_REVIEW"
    )
    monkeypatch.setattr(
        review_service, "request_review_async", AsyncMock(return_value=review(take))
    )
    assert run_review(monkeypatch, db_session, take.id) == take.pitch_id
    db_session.refresh(take)
    assert (take.status, take.review_status, take.analysis_stage) == ("COMPLETED", "DONE", "DONE")
    assert take.review_request_id is None


def test_review_claim_is_exclusive(db_session, user_id):
    take = make_ready(db_session, user_id, "REVIEWING")
    repo = TakeRepository(db_session)
    token = uuid.uuid4()
    assert repo.start_review(take.id, token)
    db_session.commit()
    assert not repo.start_review(take.id, uuid.uuid4())
    db_session.refresh(take)
    assert take.review_request_id == token


@pytest.mark.parametrize("old_fails", [False, True])
def test_replaced_worker_cannot_change_new_review(db_session, user_id, monkeypatch, old_fails):
    take = make_ready(db_session, user_id)
    calls = []

    async def respond(*args):
        calls.append(args)
        if len(calls) == 1:
            db_session.execute(
                update(Take)
                .where(Take.id == take.id)
                .values(analysis_stage_at=datetime.now(UTC) - timedelta(minutes=3))
            )
            db_session.commit()
            assert await analysis_runner._review(take.id, None, False) == take.pitch_id
            if old_fails:
                raise RuntimeError("오래된 작업 실패")
            return review(take, summary="옛 리뷰", score=10)
        return review(take)

    monkeypatch.setattr(review_service, "request_review_async", respond)
    assert run_review(monkeypatch, db_session, take.id) is None
    db_session.expire_all()
    current = db_session.get(Take, take.id)
    assert current.review_status == "DONE"
    assert (
        TakeRepository(db_session).get_ai_result(take.id, "REVIEW").payload["overall_summary"]
        == "새 리뷰"
    )
    assert db_session.get(TakeSummary, take.id).score == 90


def test_deleted_take_does_not_receive_review_or_failure(db_session, user_id, monkeypatch):
    take = make_ready(db_session, user_id)
    reply = review(take)
    take_id = take.id

    async def respond(*args):
        db_session.execute(delete(Take).where(Take.id == take_id))
        db_session.commit()
        return reply

    monkeypatch.setattr(review_service, "request_review_async", respond)
    assert run_review(monkeypatch, db_session, take_id) is None
    assert TakeRepository(db_session).get_ai_result(take_id, "REVIEW") is None
    assert db_session.query(TakeSummary).filter_by(take_id=take_id).count() == 0


def test_storage_failure_rolls_back_review_and_missions(db_session, user_id, monkeypatch):
    take = make_ready(db_session, user_id)
    db_session.add(TakeSummary(take_id=take.id, filler_count=7))
    db_session.commit()
    body = copy.deepcopy(REVIEW)
    body["take_id"] = str(take.id)
    monkeypatch.setattr(
        review_service,
        "request_review_async",
        AsyncMock(return_value=ReviewResponseDTO.model_validate(body)),
    )

    def fail(*args):
        raise RuntimeError("요약 저장 실패")

    monkeypatch.setattr(TakeRepository, "upsert_summary_review", fail)
    assert run_review(monkeypatch, db_session, take.id) == take.pitch_id
    db_session.refresh(take)
    assert take.review_status == "FAILED"
    assert TakeRepository(db_session).get_ai_result(take.id, "REVIEW") is None
    assert db_session.query(Mission).filter_by(source_take_id=take.id).count() == 0
    assert db_session.get(TakeSummary, take.id).filler_count == 7


@pytest.mark.parametrize("pending", [False, True])
def test_held_or_pending_take_cannot_claim_review(db_session, user_id, pending):
    take = make_ready(db_session, user_id, "REVIEWING")
    if pending:
        db_session.add(
            TakeAIResults(take_id=take.id, kind="SCRIPT_COVERAGE_PENDING", payload={}, meta={})
        )
    else:
        take.analysis_hold_reason = "COVERAGE_RESCORE_FAILED"
    db_session.commit()
    assert not TakeRepository(db_session).start_review(take.id, uuid.uuid4())


def test_resume_with_exhausted_budget_does_not_call_analyze(db_session, user_id, monkeypatch):
    take = make_ready(db_session, user_id, "SCORING", coverage_retry_used=True)
    in_current_session(monkeypatch, db_session)
    analyze = AsyncMock(side_effect=AssertionError("이미 쓴 한도로 호출함"))
    monkeypatch.setattr(coverage_service, "request_analyze", analyze)
    assert asyncio.run(coverage_service.analyze_script_coverage(take.id)) == "NO_COVERAGE"
    analyze.assert_not_awaited()
    db_session.refresh(take)
    assert take.coverage_retry_used is True
    assert take.analysis_stage == "REVIEWING"


def test_whole_retry_keeps_one_budget_after_restart(db_session, user_id, monkeypatch):
    take = make_ready(db_session, user_id, "SCORING")
    in_current_session(monkeypatch, db_session)
    analyze = AsyncMock(side_effect=coverage_service.CoverageCallError("장애", retryable=True))
    monkeypatch.setattr(coverage_service, "request_analyze", analyze)
    assert asyncio.run(coverage_service.analyze_script_coverage(take.id)) == "NO_COVERAGE"
    assert analyze.await_count == 2
    assert analyze.await_args_list[0].args[1] == analyze.await_args_list[1].args[1]
    db_session.execute(update(Take).where(Take.id == take.id).values(analysis_stage="SCORING"))
    db_session.commit()
    assert asyncio.run(coverage_service.analyze_script_coverage(take.id)) == "NO_COVERAGE"
    assert analyze.await_count == 2


def test_two_database_sessions_cannot_claim_one_review(engine):
    """실제 커넥션 두 개의 조건부 UPDATE에서 하나만 실행권을 얻는다."""
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from sqlalchemy.orm import Session

    from pitch_coach_backend.module.user.entity import User

    with Session(engine, expire_on_commit=False) as db:
        user = User(email=f"{uuid.uuid4()}@example.com", name="동시 실행 검사")
        db.add(user)
        db.flush()
        take = make_ready(db, user.id, "REVIEWING")
        take_id, owner_id = take.id, user.id
    barrier = Barrier(2)

    def claim(token):
        with Session(engine) as db:
            barrier.wait(timeout=5)
            claimed = TakeRepository(db).start_review(take_id, token)
            db.commit()
            return claimed

    try:
        with ThreadPoolExecutor(max_workers=2) as workers:
            outcomes = list(workers.map(claim, [uuid.uuid4(), uuid.uuid4()]))
        assert sorted(outcomes) == [False, True]
    finally:
        with Session(engine) as db:
            db.execute(delete(User).where(User.id == owner_id))
            db.commit()
