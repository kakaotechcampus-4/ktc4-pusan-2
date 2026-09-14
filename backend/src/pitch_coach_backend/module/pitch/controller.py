
from fastapi import APIRouter, UploadFile
from pitch_coach_backend.module.pitch.dto import PitchDTO, UploadPresentationDTO
from pitch_coach_backend.module.pitch.service import add_pitch_service, delete_pitch_service, update_pitch_service, upload_presentation_service

router = APIRouter(prefix="/pitches", tags=["Pitch"])

@router.post("/add")
def add_pitch(user_id, pitch_dto: PitchDTO):
    result = add_pitch_service(user_id, pitch_dto)
    return {"message": "Pitch added successfully", "pitch_id": result}

@router.put("/update/{pitch_id}")
def update_pitch(user_id, pitch_id: str, pitch_dto: PitchDTO):
    result = update_pitch_service(user_id, pitch_id, pitch_dto)
    return {"message": "Pitch updated successfully", "pitch_id": result}

@router.delete("/delete/{pitch_id}")
def delete_pitch(user_id, pitch_id: str):
    result = delete_pitch_service(user_id, pitch_id)
    return {"message": "Pitch deleted successfully", "pitch_id": result}

@router.post("/{pitch_id}/upload")
def upload_presentation(
    user_id,
    pitch_id,
    upload_presentation: UploadFile,
    script_file: UploadFile,
    description: str | None = None
):
    upload_dto = UploadPresentationDTO(
        pitch_id=pitch_id,
        user_id=user_id,
        presentation_file=upload_presentation,
        description=description
    )

    result = upload_presentation_service(user_id, upload_dto)
    return {"message": "Presentation uploaded successfully", "pitch_id": pitch_id}