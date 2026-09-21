from typing import List
import uuid
from pathlib import Path

from pitch_coach_backend.module.take.dto import TakeSummaryDTO
from sqlalchemy.orm import Session

from pitch_coach_backend.module.pitch.dto import PitchesDTO, UploadResultDTO
from pitch_coach_backend.module.pitch.entity import Pitch, PresentationVersion, ScriptVersion
from pitch_coach_backend.module.pitch.exception import NonExistentPitch
from pitch_coach_backend.module.pitch.repository import PitchRepository
from pitch_coach_backend.module.pitch.s3_service import upload
from pitch_coach_backend.module.take.repository import TakeRepository

def add_pitch_service(db: Session, user_id: uuid.UUID, pitch_dto):
    new_pitch = Pitch(
        user_id=user_id,
        title=pitch_dto.title,
        time_limit_sec=pitch_dto.time_limit_sec,
        presentation_date=pitch_dto.presentation_date
    )

    pitch_repository = PitchRepository(db)
    saved_pitch = pitch_repository.save(new_pitch)
    db.commit()

    return saved_pitch.id

def get_pitch_service(db: Session, pitch_id: uuid.UUID):
    pitch_repository = PitchRepository(db)
    existing_pitch = pitch_repository.get_by_id(pitch_id)

    if not existing_pitch:
        raise NonExistentPitch()

    return existing_pitch

# 홈 화면 : pitches 목록 조회
def get_all_pitches_service(db: Session, user_id: uuid.UUID) -> List[PitchesDTO]:
    pitch_repository = PitchRepository(db)
    take_repository = TakeRepository(db)
    pitches = pitch_repository.get_all_by_user(user_id)

    results = []
    for pitch in pitches:
        take_summaries = []
        takes = take_repository.get_takes_in_pitch(pitch.id)
        take_scores = take_repository.get_score_in_take(take.id)

        for t in range(len(takes)):
            take = takes[t]
            take_score = take_scores[t] if take_scores and t < len(take_scores) else 0
            d = None

            if t > 0:
                d = take_score - take_scores[t - 1] if take_scores and t - 1 < len(take_scores) else None
        
            take_summary = TakeSummaryDTO(
                take_version=take.take_number,
                take_elapsed=take.elapsed_time_sec,
                take_time=take.time_limit_sec,
                script_mode=take.script_mode,
                score=take_score,
                delta=d
            )

            take_summaries.append(take_summary)

        pitch_dto = PitchesDTO(
            pitch_title=pitch.title,
            pitch_time=pitch.time_limit_sec,
            thumbnail_url=None,
            takes=take_summaries
        )
        
        results.append(pitch_dto)

    return results

def update_pitch_service(db: Session, pitch_id: uuid.UUID, pitch_dto):
    pitch_repository = PitchRepository(db)
    existing_pitch = pitch_repository.get_by_id(pitch_id)

    if not existing_pitch:
        raise NonExistentPitch()

    existing_pitch.title = pitch_dto.title
    existing_pitch.time_limit_sec = pitch_dto.time_limit_sec
    existing_pitch.presentation_date = pitch_dto.presentation_date

    updated_pitch = pitch_repository.save(existing_pitch)
    db.commit()

    return updated_pitch.id

def delete_pitch_service(db: Session, pitch_id: uuid.UUID):
    pitch_repository = PitchRepository(db)
    existing_pitch = pitch_repository.get_by_id(pitch_id)

    if not existing_pitch:
        raise NonExistentPitch()

    pitch_repository.delete(existing_pitch)
    db.commit()

    return pitch_id

def upload_presentation_service(db: Session, pitch_id: uuid.UUID, upload_dto):
    pitch_repository = PitchRepository(db)

    version = pitch_repository.next_presentation_version(pitch_id)
    suffix = Path(upload_dto.presentation_file.filename or "").suffix
    presentation_key = upload(
        upload_dto.presentation_file,
        f"pitches/{pitch_id}/presentations/{version}{suffix}"
    )

    presentation = PresentationVersion(
        pitch_id=pitch_id,
        version=version,
        file_key=presentation_key,
        description=upload_dto.description
    )

    pitch_repository.save_presentation(presentation)

    return presentation.id

def upload_script_service(db: Session, pitch_id: uuid.UUID, upload_script_dto):
    pitch_repository = PitchRepository(db)

    version = pitch_repository.next_script_version(pitch_id)
    suffix = Path(upload_script_dto.script_file.filename or "").suffix
    script_key = upload(
        upload_script_dto.script_file,
        f"pitches/{pitch_id}/scripts/{version}{suffix}"
    )

    script = ScriptVersion(
        pitch_id=pitch_id,
        version=version,
        file_key=script_key
    )

    pitch_repository.save_script(script)

    # 나중에 분할 로직 들어오면 여기서 슬라이드 단위로 ScriptSlide 를 생성해야 한다.
    # ...

    return script.id

def upload_service(db: Session, pitch_id: uuid.UUID, upload_dto, upload_script_dto):
    presentation_id = upload_presentation_service(db, pitch_id, upload_dto)
    script_id = upload_script_service(db, pitch_id, upload_script_dto)

    db.commit()

    return UploadResultDTO(
        presentation_version_id=presentation_id,
        script_version_id=script_id
    )
