"""관리자 콘솔의 복구 동작. 집계(service.py)와 달리 Take 를 바꾸고 AI 를 다시 부른다.

  - 리뷰를 보류한 Take 의 병합 rescore 를 한 번 더 한다 (architecture.md 1-3 '리뷰 보류' · 1-7)
  - finalize 가 실패한 Take 의 Take 결과를 원자료로 다시 만든다 (coach-agent.md 7절 수동 복구)
  - 타이머 없이 진행 중으로 남은 Take 를 떠난 것으로 끝낸다 (멈춘 연습)

조건은 요청 안에서 확인하고, AI 를 부르는 일은 백그라운드로 띄운다. 결과는 콘솔의 Take 목록에서
본다. 언제 눌렀는지 남게 로그를 쓴다.
"""

import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from pitch_coach_backend.module import hooks
from pitch_coach_backend.module.admin import repository, service
from pitch_coach_backend.module.admin.exception import (
    FinalizeNotStarted,
    RecoveryRunning,
    TakeNotRecoverable,
)
from pitch_coach_backend.module.take import coach_service
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


# 되돌리지 못한 이유 (`coach_service.reopen_failed_finalize`)
_NOT_REOPENED = {
    "NOT_FAILED": "Take 결과를 만들지 못한 연습이 아닙니다.",
    "EXPIRED": "원자료 사용에 동의하지 않은 연습이라 끝난 지 7일이 지나 다시 만들 수 없습니다.",
    "NO_RAW_DATA": "1초 원자료가 없어 다시 만들 수 없습니다.",
}


async def retry_finalize(db: Session, take_id: uuid.UUID) -> None:
    """finalize 가 실패한 Take 를 Take 결과를 기다리는 중으로 되돌리고 finalize 를 다시 띄운다.

    성공하면 채점 · 리뷰로 이어 가고, 다시 실패하면 `FAILED` 로 돌아가 다시 누를 수 있다. 되돌린 뒤
    finalize 를 띄우지 못하면 503 이다 — Take 는 기다리는 중으로 남아 주기 작업이 이어 간다.
    """
    result = await run_in_threadpool(
        coach_service.reopen_failed_finalize, db, take_id, datetime.now(UTC)
    )
    if result is None:
        raise NonExistentTake()
    if result != "REOPENED":
        raise TakeNotRecoverable(_NOT_REOPENED[result])
    try:
        hooks.finalize_recovery().restart(take_id)
    except Exception as error:
        # 되돌리기는 커밋했다. Take 결과를 기다리는 중으로 남아 finalize 주기 작업이 이어 가니
        # FAILED 로 되돌리지 않고 그렇게 알린다
        logger.exception("되돌린 Take 의 finalize 를 띄우지 못했다 take=%s", take_id)
        raise FinalizeNotStarted() from error
    logger.info("관리자가 finalize 가 실패한 Take 의 결과를 다시 만들기 시작했다 take=%s", take_id)


def end_stuck_take(db: Session, take_id: uuid.UUID, now: datetime | None = None) -> None:
    """'멈춘 연습' 표의 Take 를 떠난 것(`ABANDONED`)으로 끝낸다. 요청 스레드에서 부른다.

    떠난 Take 타이머가 끝낼 때와 같고 끝난 시각은 마지막 활동 시각이다. 첫 연결이 저장 번호를 읽지
    못했거나 떠난 Take 를 적다 DB 오류가 나 타이머 없이 남은 Take 의 보조 수단이다. 근본 해결은
    그 경로에서 타이머를 다시 거는 것이다.
    """
    if repository.take_status(db, take_id) is None:
        raise NonExistentTake()
    active_before = service.stuck_before(now or datetime.now(UTC))
    last_active = repository.stuck_take_last_active(db, take_id, active_before)
    if last_active is None:
        raise TakeNotRecoverable("멈춘 연습이 아닙니다.")
    if not hooks.live_takes().end_stuck(take_id, last_active):
        raise TakeNotRecoverable(
            "끝내지 못했습니다. 연결이 다시 붙었거나 그 사이 끝났거나 DB 에 적지 못했습니다."
        )
    logger.info("관리자가 멈춘 연습을 떠난 것으로 끝냈다 take=%s", take_id)
