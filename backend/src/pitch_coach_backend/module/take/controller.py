from typing import Annotated

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
    update_take_service,
)

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
    return {"message": "Take deleted successfully", "take_id": result}

@router.put("/{take_id}")
def update_take(
    db: Annotated[Session, Depends(get_db)],
    take_id: OwnedTake,
    take_update_dto: TakeUpdateRequestDTO
):
    result = update_take_service(db, take_id, take_update_dto)
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
