"""다른 도메인이 Take 쪽 값을 읽는 입구. pitch 서비스가 저장소를 직접 부르지 않게 한다."""

import uuid
from datetime import datetime
from typing import Protocol

from sqlalchemy.orm import Session

from pitch_coach_backend.module.take.dto import TutorialBlock
from pitch_coach_backend.module.take.entity import Take
from pitch_coach_backend.module.take.repository import (
    ANALYSIS_STALL_AFTER,
    CONFIRMATION_WAIT,
    TakeRepository,
)
from pitch_coach_backend.module.take.states import ANALYZING, AWAITING_CONFIRMATION, RUNNING
from pitch_coach_backend.module.user import service as user_service


class TakeProgress(Protocol):
    """발표를 지워도 되는지 볼 값. `Take` 와 `TakeRepository.progress_in_pitch` 의 행이 맞는다."""

    status: str
    analysis_hold_reason: str | None
    analysis_stage: str | None
    analysis_stage_at: datetime | None


def latest_reviewed_take(db: Session, pitch_id: uuid.UUID) -> Take | None:
    return TakeRepository(db).get_latest_reviewed_take_in_pitch(pitch_id)


def latest_take(db: Session, pitch_id: uuid.UUID) -> Take | None:
    return TakeRepository(db).get_latest_take_in_pitch(pitch_id)


def _analysis_stalled(take: TakeProgress, now: datetime) -> bool:
    """사람이 복구할 때까지 끝나지 않는 분석. 운영 화면의 `list_stalled_analyses` 와 기준이 같다."""
    if take.analysis_hold_reason is not None or take.analysis_stage_at is None:
        return True
    limit = ANALYSIS_STALL_AFTER
    if take.analysis_stage == AWAITING_CONFIRMATION:
        limit += CONFIRMATION_WAIT
    return take.analysis_stage_at < now - limit


def _analysis_running(take: TakeProgress, now: datetime) -> bool:
    return take.status == ANALYZING and not _analysis_stalled(take, now)


def blocks_pitch_deletion(take: TakeProgress, now: datetime) -> bool:
    """실제로 도는 Take 면 True. 발표를 지우면 CASCADE 로 같이 지워져 연습이나 분석이 끊긴다.

    멈춘 분석은 막지 않는다. 막으면 사용자가 그 발표를 영영 지울 수 없다.
    """
    if take.status == RUNNING:
        return True
    return _analysis_running(take, now)


def pitch_has_running_analysis(db: Session, pitch_id: uuid.UUID, now: datetime) -> bool:
    """분석이 도는 Take 가 있는가. 멈춘 분석은 세지 않는다 (`blocks_pitch_deletion` 과 같다)."""
    return any(
        _analysis_running(take, now) for take in TakeRepository(db).progress_in_pitch(pitch_id)
    )


def pitch_has_take_blocking_deletion(db: Session, pitch_id: uuid.UUID, now: datetime) -> bool:
    return any(
        blocks_pitch_deletion(take, now) for take in TakeRepository(db).progress_in_pitch(pitch_id)
    )


def tutorial_block(
    db: Session, pitch_id: uuid.UUID, user_id: uuid.UUID, now: datetime
) -> TutorialBlock | None:
    """튜토리얼 샘플 발표에서 새 Take 를 막는 이유. 막지 않으면 None. 샘플 발표인지는 부르는 쪽이
    본다. Take 생성(`take._check_tutorial_take`)과 장치 점검 준비가 같이 쓴다.

    튜토리얼 리포트는 계정당 한 번이고 리포트(리뷰)가 만들어진 횟수로 센다. 끊긴 연습은 분석하지
    않아 리포트가 없으니 다시 할 수 있다. 받았다는 기록은 리뷰 결과 · Take 완료와 한 커밋이다
    (`review.save_review`). 그래서 분석 중인지를 먼저 본다 — 기록을 먼저 보면 두 조회 사이에 그
    커밋이 끼어 둘 다 지나갈 수 있다.
    """
    if pitch_has_running_analysis(db, pitch_id, now):
        return "TUTORIAL_ANALYSIS_RUNNING"
    if user_service.has_tutorial_report(db, user_id):
        return "TUTORIAL_REPORT_USED"
    return None
