"""대본 전달도 확인 (`AWAITING_CONFIRMATION`).

FE 확인 화면이 확인할 항목을 받고 답 · 건너뛰기를 보낸다. 답은 /takes/rescore 로 반영하고 리뷰로
넘긴다 (`docs/ai/architecture.md` 1-3 · 1-7, `script-coverage-evaluation.md` 12절).
"""

import logging
import uuid
from collections.abc import Callable
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from pitch_coach_backend.core.database import SessionLocal
from pitch_coach_backend.module.pitch.repository import PitchRepository
from pitch_coach_backend.module.take.ai_response import CoverageCallError, confirmation_items
from pitch_coach_backend.module.take.dto import ConfirmationAnswerDTO, ConfirmationsDTO
from pitch_coach_backend.module.take.entity import Take
from pitch_coach_backend.module.take.exception import (
    ConfirmationClosed,
    ConfirmationRescoreFailed,
    InvalidConfirmationAnswer,
    NonExistentTake,
)
from pitch_coach_backend.module.take.repository import CONFIRMATION_WAIT, TakeRepository
from pitch_coach_backend.module.take.service import analysis_runner
from pitch_coach_backend.module.take.service import coverage as coverage_service

logger = logging.getLogger(__name__)


def _awaiting(take: Take) -> bool:
    return (
        take.status == "ANALYZING"
        and take.analysis_stage == "AWAITING_CONFIRMATION"
        and take.analysis_hold_reason is None
    )


def _rubrics(db: Session, take: Take) -> dict[int, Any]:
    slides = PitchRepository(db).get_slides(take.script_version_id)
    return {slide.slide_number: slide.rubric for slide in slides}


def get_confirmations_service(
    db: Session, take_id: uuid.UUID, user_id: uuid.UUID
) -> ConfirmationsDTO:
    take_repository = TakeRepository(db)
    take = take_repository.get_owned(take_id, user_id)
    if take is None:
        raise NonExistentTake()
    confirmed = take_repository.get_ai_result(take_id, coverage_service.COVERAGE_KIND)
    if not _awaiting(take) or confirmed is None:
        return ConfirmationsDTO(
            take_id=take.id, analysis_stage=take.analysis_stage, remaining_ms=None, items=[]
        )
    # 단계를 바꿀 때 늘 적지만 비어 있으면 기한이 지난 것으로 본다 (분석 실행기도 기다리지 않는다)
    started_at = take.analysis_stage_at or take_repository.clock() - CONFIRMATION_WAIT
    left = started_at + CONFIRMATION_WAIT - take_repository.clock()
    return ConfirmationsDTO(
        take_id=take.id,
        analysis_stage=take.analysis_stage,
        remaining_ms=max(0, int(left.total_seconds() * 1000)),
        items=confirmation_items(confirmed.payload, _rubrics(db, take)),
    )


async def _in_session[T](
    session_factory: Callable[[], Session], fn: Callable[..., T], *args: Any
) -> T:
    def call() -> T:
        db = session_factory()
        try:
            return fn(db, *args)
        finally:
            db.close()

    return await run_in_threadpool(call)


def _skip(db: Session, take_id: uuid.UUID, user_id: uuid.UUID) -> None:
    take_repository = TakeRepository(db)
    if take_repository.get_owned(take_id, user_id) is None:
        raise NonExistentTake()
    if not take_repository.skip_confirmation(take_id):
        db.rollback()
        raise ConfirmationClosed()
    db.commit()


def _check_answers(payload: dict, answers: list[ConfirmationAnswerDTO]) -> None:
    open_items = {(item["slide_number"], item["item_id"]) for item in payload["confirmations"]}
    keys = [(answer.slide_number, answer.item_id) for answer in answers]
    if len(keys) != len(set(keys)) or not set(keys) <= open_items:
        raise InvalidConfirmationAnswer()


def _claim(
    db: Session, take_id: uuid.UUID, user_id: uuid.UUID, answers: list[ConfirmationAnswerDTO]
) -> dict:
    """표식을 잡고 rescore 재료를 읽는다. 답이 맞지 않으면 표식을 잡지 않은 것으로 되돌린다."""
    take_repository = TakeRepository(db)
    take = take_repository.get_owned(take_id, user_id)
    if take is None:
        raise NonExistentTake()
    requested_at = take_repository.claim_confirmation(take_id)
    confirmed = take_repository.get_ai_result(take_id, coverage_service.COVERAGE_KIND)
    if requested_at is None or confirmed is None:
        db.rollback()
        raise ConfirmationClosed()
    try:
        _check_answers(confirmed.payload, answers)
    except InvalidConfirmationAnswer:
        db.rollback()
        raise
    job = {
        "requested_at": requested_at,
        "revision": confirmed.revision,
        "body": {
            "take_id": str(take_id),
            "evaluations": confirmed.payload["evaluations"],
            "rubrics": [rubric for rubric in _rubrics(db, take).values() if rubric is not None],
            "answers": [answer.model_dump() for answer in answers],
            "failed": confirmed.payload["failed"],
        },
    }
    db.commit()
    return job


def _apply(
    db: Session, take_id: uuid.UUID, requested_at: datetime, revision: int, reply: dict
) -> bool:
    """답을 반영한 채점으로 확정본을 덮고 `REVIEWING` 으로 넘긴다.

    표식과 읽은 revision 이 그대로일 때만 반영한다.
    """
    take_repository = TakeRepository(db)
    payload = {key: value for key, value in reply.items() if key != "meta"}
    take = take_repository.lock_confirmation(take_id, requested_at)
    if take is None:
        db.rollback()
        return False
    if not take_repository.replace_ai_result(
        take_id, coverage_service.COVERAGE_KIND, revision, payload, reply.get("meta")
    ):
        # 표식 아래에서는 생기지 않지만 생기면 표식을 놓아 리뷰가 막히지 않게 한다
        db.rollback()
        _release(db, take_id, requested_at)
        return False
    take_repository.upsert_summary_speak_accuracy(take_id, coverage_service.speak_accuracy(payload))
    take_repository.set_analysis_stage(take_id, "REVIEWING")
    take.coverage_requested_at = None
    db.commit()
    return True


def _release(db: Session, take_id: uuid.UUID, requested_at: datetime) -> None:
    TakeRepository(db).release_confirmation(take_id, requested_at)
    db.commit()


async def _rescore(take_id: uuid.UUID, body: dict) -> dict:
    # 같은 답을 다시 보내도 코어가 처음 반영한 답을 지키므로 같은 요청 ID 로 한 번 더 부른다
    request_id = uuid.uuid4().hex
    try:
        return await coverage_service.request_rescore(body, request_id)
    except CoverageCallError as e:
        logger.warning("take %s: confirmation rescore failed: %s", take_id, e)
        if not e.retryable:
            raise
    return await coverage_service.request_rescore(body, request_id)


async def send_confirmations_service(
    take_id: uuid.UUID,
    user_id: uuid.UUID,
    answers: list[ConfirmationAnswerDTO],
    session_factory: Callable[[], Session] = SessionLocal,
) -> str:
    """확인 답을 반영하거나(빈 답이면 건너뛰고) 리뷰로 넘긴다. 넘긴 분석 단계를 돌려준다.

    rescore 를 기다리는 동안 DB 세션을 쥐지 않는다. 그동안은 표식이 리뷰 시작을 막는다.
    """
    if not answers:
        await _in_session(session_factory, _skip, take_id, user_id)
    else:
        job = await _in_session(session_factory, _claim, take_id, user_id, answers)
        try:
            reply = await _rescore(take_id, job["body"])
        except CoverageCallError as e:
            logger.warning("take %s: confirmation rescore gave up: %s", take_id, e)
            await _in_session(session_factory, _release, take_id, job["requested_at"])
            raise ConfirmationRescoreFailed() from e
        if not await _in_session(
            session_factory, _apply, take_id, job["requested_at"], job["revision"], reply
        ):
            raise ConfirmationClosed()
    # 확인을 기다리던 분석 실행기가 단계가 바뀐 것을 보고 리뷰한다.
    # 실행기가 없으면(재시작 등) 띄운다
    analysis_runner.start(take_id)
    return "REVIEWING"
