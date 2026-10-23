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
from pitch_coach_backend.module.take.service import (
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
    result = create_take_service(db, pitch_id, take_dto)
    return {"message": "Take created successfully", "take_id": result}

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