import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from pitch_coach_backend.module.take.dto import TakeInitRequestDTO, TakeUpdateRequestDTO
from sqlalchemy.orm import Session

from pitch_coach_backend.module.take.dto import TakeInitRequestDTO, CalibrationDTO
from pitch_coach_backend.core.database import get_db
from pitch_coach_backend.module.pitch.dependencies import OwnedPitch
from pitch_coach_backend.module.take.service import delete_take_service, create_take_service, update_take_service, create_calibration_service

router = APIRouter(prefix="/pitches/{pitch_id}/takes", tags=["Take"])

@router.post("/")
def create_take(
    pitch_id: OwnedPitch,
    db: Annotated[Session, Depends(get_db)],
    take_dto: TakeInitRequestDTO
):
    take_init_dto = TakeInitRequestDTO(**take_dto)
    result = create_take_service(db, pitch_id, take_init_dto)
    return {"message": "Take created successfully", "take_id": result}

@router.delete("/{take_id}")
def delete_take(
    pitch_id: OwnedPitch,
    db: Annotated[Session, Depends(get_db)],
    take_id: uuid.UUID
):
    result = delete_take_service(db, pitch_id, take_id)
    return {"message": "Take deleted successfully", "take_id": result}

@router.put("/{take_id}")
def update_take(
    pitch_id: OwnedPitch,
    db: Annotated[Session, Depends(get_db)],
    take_id: uuid.UUID,
    take_update_dto: TakeUpdateRequestDTO
):
    result = update_take_service(db, pitch_id, take_id, take_update_dto)
    return {"message": "Take updated successfully", "take_id": result}

@router.post("/{take_id}/calibration")
def create_calibration(
    pitch_id: OwnedPitch,
    db: Annotated[Session, Depends(get_db)],
    take_id: uuid.UUID,
    calibration_dto: CalibrationDTO
):
    result = create_calibration_service(db, pitch_id, take_id, calibration_dto)
    return {"message": "Calibration created successfully", "calibration_id": result}
