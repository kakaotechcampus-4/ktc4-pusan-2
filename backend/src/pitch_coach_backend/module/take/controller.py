import logging
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from pitch_coach_backend.core.database import get_db
from pitch_coach_backend.module import hooks
from pitch_coach_backend.module.auth.dependencies import CurrentUser
from pitch_coach_backend.module.pitch.dependencies import OwnedPitch
from pitch_coach_backend.module.take.dependencies import OwnedTake
from pitch_coach_backend.module.take.dto import (
    CalibrationDTO,
    TakeHistoryResponseDTO,
    TakeInitRequestDTO,
    TakeUpdateRequestDTO,
)
from pitch_coach_backend.module.take.exception import TakeInProgress, TakeNotReviewable
from pitch_coach_backend.module.take.service import (
    close_take_service,
    create_calibration_service,
    create_take_service,
    delete_take_service,
    get_previous_missions_service,
    get_review_result,
    list_takes_service,
    mark_ended,
    update_take_service,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/pitches/{pitch_id}/takes", tags=["Take"])

@router.post("/")
def create_take(
    pitch_id: OwnedPitch,
    db: Annotated[Session, Depends(get_db)],
    take_dto: TakeInitRequestDTO
):
    take = create_take_service(db, pitch_id, take_dto)
    # 번호는 FE 가 장치 점검에서 미리 본 next_take_number 와 다를 수 있다. 이 값이 실제 번호다
    return {
        "message": "Take created successfully",
        "take_id": take.id,
        "take_number": take.take_number,
    }

@router.delete("/{take_id}")
def delete_take(
    take_id: OwnedTake,
    db: Annotated[Session, Depends(get_db)]
):
    result = delete_take_service(db, take_id)
    # 진행 중이던 연습이면 실시간 STT(Deepgram) · 코치도 끊는다
    hooks.live_takes().discard([take_id])
    return {"message": "Take deleted successfully", "take_id": result}

@router.put("/{take_id}")
def update_take(
    db: Annotated[Session, Depends(get_db)],
    take_id: OwnedTake,
    take_update_dto: TakeUpdateRequestDTO,
):
    result = update_take_service(db, take_id, take_update_dto)
    # 발표를 끝냈다. 보통은 WS stop 이 마지막 전사까지 저장한 뒤 Take 를 끝낸다. 발표 중에 연결이
    # 끊겨 STT 없이 끝낸 FE 는 stop 을 못 보내므로 여기서 끝낸다 — 남은 전사 · 원자료를 저장한 뒤
    # 이 요청의 DB 세션으로 끝난 것으로 적는다. 적지 못하면 실패로 답한다 (FE 는 다시 보낸다).
    # 시작하지 않은 Take(READY)는 update_take_service 가 먼저 거절한다
    if take_update_dto.ended_at is not None:
        hooks.live_takes().end(
            take_id,
            lambda end_reason, utterance_ends: mark_ended(db, take_id, end_reason, utterance_ends),
        )
    return {"message": "Take updated successfully", "take_id": result}

@router.post("/{take_id}/abandon")
def abandon_take(
    db: Annotated[Session, Depends(get_db)],
    take_id: OwnedTake,
):
    # 창을 닫았다 다시 연 FE 가 부른다. 창을 닫은 Take 는 이어 가지 않고 실패로 끝낸다.
    # 다른 창이 아직 발표 중이면 거절한다 — FE 는 하트비트로 보지만 늦을 수 있다
    if hooks.live_takes().is_live(take_id):
        raise TakeInProgress()
    status, closed = close_take_service(db, take_id)
    if closed:
        # 남은 스트림 · 떠난 Take 타이머 · 코치를 정리한다
        hooks.live_takes().end_closed_tab(take_id)
    return {"message": "Take abandoned", "take_id": take_id, "status": status}

@router.post("/{take_id}/calibration")
def create_calibration(
    db: Annotated[Session, Depends(get_db)],
    take_id: OwnedTake,
    calibration_dto: CalibrationDTO
):
    result = create_calibration_service(db, take_id, calibration_dto)
    return {"message": "Calibration created successfully", "calibration_id": result}

@router.get("/previous-missions")
def get_previous_missions(
    pitch_id: OwnedPitch,
    db: Annotated[Session, Depends(get_db)],
):
    result = get_previous_missions_service(db, pitch_id)
    return {"message": "Previous missions retrieved successfully", "missions": result}

@router.post("/{take_id}/review")
def review_take(
    db: Annotated[Session, Depends(get_db)],
    pitch_id: uuid.UUID,
    take_id: OwnedTake,
):
    def plan_next_take() -> None:
        # 리뷰를 저장한 직후, 리뷰가 끝내 실패해도 다음 Take 의 코칭 계획을 만든다
        # (coach-agent.md 5-1). 백그라운드라 응답을 기다리게 하지 않는다. 계획을 못 띄워도 리뷰
        # 결과 · 원래 오류를 가리지 않는다
        try:
            hooks.coaching().plan_next_take(pitch_id)
        except Exception:
            logger.exception("리뷰 뒤 코칭 계획을 띄우지 못했다 pitch=%s", pitch_id)

    try:
        result = get_review_result(db, take_id)
    except TakeNotReviewable:
        # 리뷰를 부르지 않았다 (아직 채점 전 · 이미 리뷰함)
        raise
    except Exception:
        plan_next_take()
        raise
    plan_next_take()
    return {"message": "Review result retrieved successfully", "review_result": result}


# 발표를 가리지 않는 Take 조회. Take 기록은 모든 발표의 Take 를 한 번에 보여 준다
takes_router = APIRouter(prefix="/takes", tags=["Take"])

@takes_router.get("/", response_model=TakeHistoryResponseDTO)
def list_takes(
    current_user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
    pitch_id: uuid.UUID | None = None,
):
    return list_takes_service(db, current_user.id, pitch_id)

@takes_router.get("/{take_id}/report")
def get_take_report(
    db: Annotated[Session, Depends(get_db)],
    take_id: OwnedTake,
):
    return get_take_report_service(db, take_id)