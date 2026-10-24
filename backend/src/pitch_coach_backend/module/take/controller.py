from typing import Annotated

import anyio
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from pitch_coach_backend.core.database import get_db
from pitch_coach_backend.module.pitch.dependencies import OwnedPitch
from pitch_coach_backend.module.take.dependencies import OwnedTake
from pitch_coach_backend.module.take.dto import (
    CalibrationDTO,
    TakeInitRequestDTO,
    TakeUpdateRequestDTO,
)
from pitch_coach_backend.module.take.exception import TakeInProgress
from pitch_coach_backend.module.take.service import (
    close_take_service,
    create_calibration_service,
    create_take_service,
    delete_take_service,
    get_previous_missions_service,
    get_review_result,
    mark_ended,
    update_take_service,
)
from pitch_coach_backend.realtime import take_stream

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
    # 진행 중이던 연습이면 실시간 STT(Deepgram)도 끊는다. 스트림은 이벤트 루프에 있어서
    # 이 함수(스레드 풀)에서 바로 만지지 않고 루프로 넘긴다
    anyio.from_thread.run(take_stream.discard, [take_id])
    return {"message": "Take deleted successfully", "take_id": result}

@router.put("/{take_id}")
def update_take(
    db: Annotated[Session, Depends(get_db)],
    take_id: OwnedTake,
    take_update_dto: TakeUpdateRequestDTO
):
    result = update_take_service(db, take_id, take_update_dto)
    # 발표를 끝냈다. 보통은 WS stop 이 마지막 전사까지 저장한 뒤 Take 를 끝낸다. STT 없이 끝낸
    # FE 는 stop 을 못 보내므로 여기서 끝낸다. 스트림은 이벤트 루프에 있어서 루프로 넘긴다
    if take_update_dto.ended_at is not None and anyio.from_thread.run(
        take_stream.end_without_stop, take_id
    ):
        mark_ended(db, take_id)
    return {"message": "Take updated successfully", "take_id": result}

@router.post("/{take_id}/abandon")
def abandon_take(
    db: Annotated[Session, Depends(get_db)],
    take_id: OwnedTake,
):
    # 창을 닫았다 다시 연 FE 가 부른다. 창을 닫은 Take 는 이어 가지 않고 실패로 끝낸다.
    # 다른 창이 아직 발표 중이면 거절한다 — FE 는 하트비트로 보지만 늦을 수 있다
    if anyio.from_thread.run_sync(take_stream.is_live, take_id):
        raise TakeInProgress()
    status, closed = close_take_service(db, take_id)
    if closed:
        # 남은 스트림 · 떠난 Take 타이머를 끊는다. 스트림은 이벤트 루프에 있어서 루프로 넘긴다
        anyio.from_thread.run(take_stream.end_closed_tab, take_id)
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
    take_id: OwnedTake,
):
    result = get_review_result(db, take_id)
    return {"message": "Review result retrieved successfully", "review_result": result}