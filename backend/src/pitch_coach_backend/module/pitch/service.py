import uuid
from pathlib import Path

from sqlalchemy.orm import Session

from pitch_coach_backend.module.pitch.dto import UploadResultDTO, VersionDTO, VersionSummaryDTO
from pitch_coach_backend.module.pitch.entity import Pitch, PresentationVersion, ScriptVersion
from pitch_coach_backend.module.pitch.exception import NonExistentPitch
from pitch_coach_backend.module.pitch.repository import PitchRepository
from pitch_coach_backend.module.pitch.s3_service import upload


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

# 관련 자료(발표자료, 대본, 평가) 들고 오기
def get_pitch_datas(db: Session, pitch_id: uuid.UUID):
    pitch_repository = PitchRepository(db)
    existing_pitch = pitch_repository.get_by_id(pitch_id)

    if not existing_pitch:
        raise NonExistentPitch()

    presentation_versions = get_presentation_versions(db, pitch_id)
    script_versions = get_script_versions(db, pitch_id)   
    evaluations = get_evaluation_versions(db, pitch_id)

    return VersionSummaryDTO(
        pitch_id=pitch_id,
        presentation_versions=presentation_versions,
        script_versions=script_versions,
        evaluation_versions=evaluations
    )

# 발표자료 기본 정보들 들고 오기
# 근데 발표자료, 대본, 평가 들고 오는 로직이 다 비슷한 것 같은데....?
def get_presentation_versions(db: Session, pitch_id: uuid.UUID):
    pitch_repository = PitchRepository(db)
    existing_pitch = pitch_repository.get_by_id(pitch_id)

    if not existing_pitch:
        raise NonExistentPitch()

    presentation_versions = pitch_repository.get_presentations(pitch_id)

    presentation_version_summaries = []

    for version in presentation_versions:
        presentation_version_summaries.append(
            VersionDTO(
                id=version.id,
                version=version.version
            )
        )
                
    return presentation_version_summaries

# 대본 기본 정보들 들고 오기
def get_script_versions(db: Session, pitch_id: uuid.UUID):
    pitch_repository = PitchRepository(db)
    existing_pitch = pitch_repository.get_by_id(pitch_id)

    if not existing_pitch:
        raise NonExistentPitch()

    script_versions = pitch_repository.get_scripts(pitch_id)
    script_version_summaries = []

    if script_versions:
        for script_version in script_versions:
            script_version_summaries.append(
            VersionDTO(
                id=script_version.id,
                version=script_version.version
            )
        )

    return script_version_summaries

def get_evaluation_versions(db: Session, pitch_id: uuid.UUID):
    pitch_repository = PitchRepository(db)
    existing_pitch = pitch_repository.get_by_id(pitch_id)

    if not existing_pitch:
        raise NonExistentPitch()

    evaluations = pitch_repository.get_evaluations(pitch_id)

    evaluation_summaries = []
    for evaluation in evaluations:

        evaluation_summaries.append(
            VersionDTO(
                id=evaluation.id,
                version=evaluation.version
            )
        )
    return evaluation_summaries

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
