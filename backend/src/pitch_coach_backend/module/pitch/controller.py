
from typing import Annotated

from fastapi import APIRouter, Depends, Form, UploadFile
from pitch_coach_backend.module.pitch.repository import PitchRepository
from sqlalchemy.orm import Session

from pitch_coach_backend.core.database import get_db
from pitch_coach_backend.module.auth.dependencies import CurrentUser
from pitch_coach_backend.module.pitch.dependencies import OwnedPitch
from pitch_coach_backend.module.pitch.dto import PitchDTO, UploadPresentationDTO, UploadScriptDTO
from pitch_coach_backend.module.pitch.service import (
    add_pitch_service,
    delete_pitch_service,
    get_all_pitches_service,
    get_pitch_service,
    update_pitch_service,
    upload_service
)

router = APIRouter(prefix="/pitches", tags=["Pitch"])

@router.get("/")
def get_pitches(
    current_user: CurrentUser,
    db: Annotated[Session, Depends(get_db)]
):
    all_pitches = get_all_pitches_service(db, current_user.id)
    return all_pitches


@router.get("/{pitch_id}")
def get_pitch(
    pitch_id: OwnedPitch,
    db: Annotated[Session, Depends(get_db)]
):
    pitch = get_pitch_service(db, pitch_id)
    return pitch

@router.post("/add")
def add_pitch(
    current_user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
    pitch_dto: PitchDTO
):
    result = add_pitch_service(db, current_user.id, pitch_dto)
    return {"message": "Pitch added successfully", "pitch_id": result}

@router.put("/update/{pitch_id}")
def update_pitch(
    pitch_id: OwnedPitch,
    db: Annotated[Session, Depends(get_db)],
    pitch_dto: PitchDTO
):
    result = update_pitch_service(db, pitch_id, pitch_dto)
    return {"message": "Pitch updated successfully", "pitch_id": result}

@router.delete("/delete/{pitch_id}")
def delete_pitch(
    pitch_id: OwnedPitch,
    db: Annotated[Session, Depends(get_db)]
):
    result = delete_pitch_service(db, pitch_id)
    return {"message": "Pitch deleted successfully", "pitch_id": result}

@router.post("/{pitch_id}/upload")
def upload_presentation(
    pitch_id: OwnedPitch,
    db: Annotated[Session, Depends(get_db)],
    presentation_file: UploadFile,
    script_file: UploadFile,
    description: Annotated[str | None, Form()] = None
):
    upload_presentation_dto = UploadPresentationDTO(
        presentation_file=presentation_file,
        description=description
    )

    upload_script_dto = UploadScriptDTO(
        script_file=script_file
    )

    result = upload_service(db, pitch_id, upload_presentation_dto, upload_script_dto)

    return {
        "message": "Presentation uploaded successfully",
        "pitch_id": pitch_id,
        "presentation_version_id": result.presentation_version_id,
        "script_version_id": result.script_version_id
    }
