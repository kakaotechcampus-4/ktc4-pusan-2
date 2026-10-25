"""채점 작업은 Take 마다 하나만 돌고, 이어받힌 작업은 아무것도 바꾸지 못한다.

`takes.coverage_requested_at` 표식 (`docs/ai/architecture.md` 1-7 Take별 직렬화).
"""

import asyncio
from datetime import timedelta
from unittest.mock import AsyncMock

from sqlalchemy import func, update

from pitch_coach_backend.module.pitch.entity import ScriptSlide
from pitch_coach_backend.module.take.ai_response import CoverageCallError
from pitch_coach_backend.module.take.entity import Take
from pitch_coach_backend.module.take.repository import COVERAGE_STALE_AFTER, TakeRepository
from pitch_coach_backend.module.take.service import coverage as coverage_service
from tests.test_analysis_review_ownership import in_current_session, make_ready
from tests.test_take_ai_validation import claim, coverage


def _scoring(db, user_id) -> Take:
    take = make_ready(db, user_id, "SCORING")
    db.add(
        ScriptSlide(
            script_version_id=take.script_version_id,
            slide_number=1,
            full_content="합성 대본",
            rubric={"rubric_id": "rubric-1", "slide_number": 1},
        )
    )
    db.commit()
    return take


def test_duplicate_job_does_not_discard_the_first_valid_result(db_session, user_id, monkeypatch):
    """기다리던 작업의 정상 응답을 먼저 끝난 중복 작업의 실패가 버리게 하지 않는다."""
    take = _scoring(db_session, user_id)
    in_current_session(monkeypatch, db_session)
    calls = 0

    async def run():
        nonlocal calls
        entered, answer = asyncio.Event(), asyncio.Event()

        async def respond(*args):
            nonlocal calls
            calls += 1
            if calls == 1:
                entered.set()
                await answer.wait()
                return coverage(take.id)
            raise CoverageCallError("synthetic failure", retryable=False)

        monkeypatch.setattr(coverage_service, "request_analyze", respond)
        first = asyncio.create_task(coverage_service.analyze_script_coverage(take.id))
        await entered.wait()
        duplicate = await coverage_service.analyze_script_coverage(take.id)
        answer.set()
        return duplicate, await first

    assert asyncio.run(run()) == ("SKIPPED", "READY_FOR_REVIEW")
    assert calls == 1
    assert TakeRepository(db_session).get_ai_result(take.id, "SCRIPT_COVERAGE") is not None
    db_session.refresh(take)
    assert (take.analysis_stage, take.coverage_requested_at) == ("REVIEWING", None)


def test_taken_over_job_cannot_save_fail_or_move_the_stage(db_session, user_id):
    take = _scoring(db_session, user_id)
    old = claim(db_session, take.id)
    new = TakeRepository(db_session).claim_coverage(take.id, takeover=True)
    assert new is not None and new != old
    # 저장 함수가 실패하며 rollback 해도 표식이 남게 커밋해 둔다
    db_session.commit()
    reply = coverage(take.id)
    candidate = {
        "script_version_id": str(take.script_version_id),
        "base_revision": 0,
        "evaluations": reply["evaluations"],
        "failed": [],
        "retry_meta": reply["meta"],
    }

    assert not coverage_service._mark_retry_used(db_session, take.id, old, "coverage_retry_used")
    assert coverage_service._save_confirmed(db_session, take.id, old, reply) is None
    assert not coverage_service._save_candidate(db_session, take.id, old, candidate)
    assert not coverage_service._apply_merged(db_session, take.id, old, 0, reply)
    coverage_service._hold_coverage(db_session, take.id, old)
    assert coverage_service._conclude_coverage(db_session, take.id, old) == "SKIPPED"

    db_session.refresh(take)
    assert (take.analysis_stage, take.analysis_hold_reason, take.coverage_retry_used) == (
        "SCORING",
        None,
        False,
    )
    assert take.coverage_requested_at == new
    assert TakeRepository(db_session).get_ai_result(take.id, "SCRIPT_COVERAGE") is None
    # 이어받은 작업은 그대로 쓴다
    assert coverage_service._save_confirmed(db_session, take.id, new, reply) is not None
    assert coverage_service._conclude_coverage(db_session, take.id, new) == "READY_FOR_REVIEW"


def test_running_job_is_not_taken_over_until_it_goes_stale(db_session, user_id):
    take = _scoring(db_session, user_id)
    repo = TakeRepository(db_session)
    first = claim(db_session, take.id)

    assert repo.claim_coverage(take.id) is None

    db_session.execute(
        update(Take)
        .where(Take.id == take.id)
        .values(coverage_requested_at=func.now() - COVERAGE_STALE_AFTER - timedelta(seconds=1))
    )
    taken = repo.claim_coverage(take.id)
    assert taken is not None and taken != first


def test_restart_takes_over_a_job_cut_off_by_the_restart(db_session, user_id, monkeypatch):
    take = _scoring(db_session, user_id)
    claim(db_session, take.id)
    db_session.commit()
    in_current_session(monkeypatch, db_session)
    analyze = AsyncMock(return_value=coverage(take.id))
    monkeypatch.setattr(coverage_service, "request_analyze", analyze)

    assert asyncio.run(coverage_service.analyze_script_coverage(take.id)) == "SKIPPED"
    analyze.assert_not_awaited()

    outcome = asyncio.run(coverage_service.analyze_script_coverage(take.id, takeover=True))

    assert outcome == "READY_FOR_REVIEW"
    analyze.assert_awaited_once()


def test_held_job_lets_go_of_its_mark(db_session, user_id):
    take = _scoring(db_session, user_id)
    requested_at = claim(db_session, take.id)
    db_session.commit()

    coverage_service._hold_coverage(db_session, take.id, requested_at)

    assert coverage_service._conclude_coverage(db_session, take.id, requested_at) == "HELD"
    db_session.refresh(take)
    assert take.analysis_hold_reason == "COVERAGE_RESCORE_FAILED"
    assert take.coverage_requested_at is None
    # 보류된 Take 는 사람이 정한 복구 전에는 다시 잡히지 않는다
    assert TakeRepository(db_session).claim_coverage(take.id) is None
