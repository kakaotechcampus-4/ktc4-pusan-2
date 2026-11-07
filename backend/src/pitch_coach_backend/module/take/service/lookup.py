"""다른 도메인이 Take 쪽 값을 읽는 입구. pitch 서비스가 저장소를 직접 부르지 않게 한다."""

import uuid
from datetime import datetime
from typing import Protocol

from sqlalchemy.orm import Session

from pitch_coach_backend.module.take.entity import Take
from pitch_coach_backend.module.take.repository import (
    ANALYSIS_STALL_AFTER,
    CONFIRMATION_WAIT,
    TakeRepository,
)
from pitch_coach_backend.module.take.states import ANALYZING, AWAITING_CONFIRMATION, RUNNING


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


def blocks_pitch_deletion(take: TakeProgress, now: datetime) -> bool:
    """실제로 도는 Take 면 True. 발표를 지우면 CASCADE 로 같이 지워져 연습이나 분석이 끊긴다.

    멈춘 분석은 막지 않는다. 막으면 사용자가 그 발표를 영영 지울 수 없다.
    """
    if take.status == RUNNING:
        return True
    return take.status == ANALYZING and not _analysis_stalled(take, now)


def pitch_has_take_blocking_deletion(db: Session, pitch_id: uuid.UUID, now: datetime) -> bool:
    return any(
        blocks_pitch_deletion(take, now) for take in TakeRepository(db).progress_in_pitch(pitch_id)
    )
