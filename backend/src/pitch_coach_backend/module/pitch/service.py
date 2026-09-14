from pitch_coach_backend.module.pitch.entity import Pitch, PresentationVersion
from pitch_coach_backend.module.pitch.repository import PitchRepository
from pitch_coach_backend.module.pitch.exception import InvalidAuthorizationRequest, NonExistentPitch

def add_pitch_service(user_id, pitch_dto):
    if not user_id:
        raise InvalidAuthorizationRequest() 
    
    new_pitch = Pitch(
        title=pitch_dto.title,
        time_limit_sec=pitch_dto.time_limit_sec,
        presentation_date=pitch_dto.presentation_date
    )
    
    # DB에 저장 (PitchRepository를 통해)
    pitch_repository = PitchRepository()
    saved_pitch = pitch_repository.save(new_pitch)
    
    return saved_pitch.id  # 저장된 Pitch의 ID를 반환

def update_pitch_service(user_id, pitch_id, pitch_dto):
    if not user_id:
        raise InvalidAuthorizationRequest()
    
    pitch_repository = PitchRepository()
    existing_pitch = pitch_repository.get_by_id(pitch_id)
    
    if not existing_pitch:
        raise NonExistentPitch()
    
    # Update the existing pitch with new data
    existing_pitch.title = pitch_dto.title
    existing_pitch.time_limit_sec = pitch_dto.time_limit_sec
    existing_pitch.presentation_date = pitch_dto.presentation_date
    
    # Save the updated pitch
    updated_pitch = pitch_repository.save(existing_pitch)
    
    return updated_pitch.id  # 저장된 Pitch의 ID를 반환

def delete_pitch_service(user_id, pitch_id):
    if not user_id:
        raise InvalidAuthorizationRequest()
    
    pitch_repository = PitchRepository()
    existing_pitch = pitch_repository.get_by_id(pitch_id)
    
    if not existing_pitch:
        raise NonExistentPitch()
    
    # Delete the pitch
    pitch_repository.delete(existing_pitch)
    
    return pitch_id  # 삭제된 Pitch의 ID를 반환

def upload_presentation_service(user_id, upload_dto):
    if not user_id:
        raise InvalidAuthorizationRequest()

    pitch_repository = PitchRepository()
    existing_pitch = pitch_repository.get_by_id(upload_dto.pitch_id)

    if not existing_pitch:
        raise NonExistentPitch()

    presentation = PresentationVersion(
        pitch_id=upload_dto.pitch_id,
        user_id=upload_dto.user_id,
        file=upload_dto.presentation_file,
        description=upload_dto.description
    )

    pitch_repository.save_presentation(presentation)
    return presentation.id
