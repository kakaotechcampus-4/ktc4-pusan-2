"""관리자 콘솔의 복구 동작. 집계(service.py)와 달리 Take 를 바꾸고 AI 를 다시 부른다.

  - 리뷰를 보류한 Take 의 병합 rescore 를 한 번 더 한다 (architecture.md 1-3 '리뷰 보류' · 1-7)

조건은 요청 안에서 확인하고, AI 를 부르는 일은 백그라운드로 띄운다. 결과는 콘솔의 Take 목록에서
본다. 언제 눌렀는지 남게 로그를 쓴다.
"""

import logging
import uuid

from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from pitch_coach_backend.module.admin.exception import RecoveryRunning, TakeNotRecoverable
from pitch_coach_backend.module.take.exception import NonExistentTake
from pitch_coach_backend.module.take.service import analysis_runner
from pitch_coach_backend.module.take.service import coverage as coverage_service

logger = logging.getLogger(__name__)


async def rescore_held_coverage(db: Session, take_id: uuid.UUID) -> None:
    """리뷰를 보류한 Take 의 병합 rescore 를 백그라운드로 다시 시작한다.

    성공하면 보류가 풀리고 확인 · 리뷰로 이어 간다. 실패하면 보류가 그대로 남아 다시 누를 수 있다.
    """
    state = await run_in_threadpool(coverage_service.held_recovery, db, take_id)
    if state is None:
        raise NonExistentTake()
    if state == "NOT_HELD":
        raise TakeNotRecoverable("리뷰를 보류한 연습이 아닙니다.")
    if state == "BUSY" or analysis_runner.recover_held(take_id) is None:
        raise RecoveryRunning()
    logger.info("관리자가 보류한 Take 의 병합 재채점을 다시 시작했다 take=%s", take_id)
