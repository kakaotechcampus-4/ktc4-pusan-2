import uuid

from sqlalchemy.orm import Session

from pitch_coach_backend.module.pitch.repository import PitchRepository
from pitch_coach_backend.module.take.dto import CalibrationDTO, MissionDTO, PreviousMissionsDTO, TakeInitRequestDTO, TakeUpdateRequestDTO
from pitch_coach_backend.module.take.entity import Calibration, Take
from pitch_coach_backend.module.take.exception import NonExistentTake
from pitch_coach_backend.module.take.repository import TakeRepository


# Take 생성, 삭제, 업데이트 서비스 함수들 정의.
def create_take_service(db: Session, pitch_id: uuid.UUID, take_dto: TakeInitRequestDTO):
    take_repository = TakeRepository(db)

    if take_repository.get_presentation_version_in_pitch(take_dto.presentation_version_id, pitch_id) is None:
        raise NonExistentTake()

    if take_repository.get_script_version_in_pitch(take_dto.script_version_id, pitch_id) is None:
        raise NonExistentTake()

    new_take = Take(
        pitch_id=pitch_id,
        mode=take_dto.mode,
        script_mode=take_dto.script_mode,
        presentation_version_id=take_dto.presentation_version_id,
        script_version_id=take_dto.script_version_id,
    )

    saved_take = take_repository.save(new_take)
    db.commit()

    return saved_take.id


def delete_take_service(db: Session, pitch_id: uuid.UUID, take_id: uuid.UUID):
    take_repository = TakeRepository(db)
    existing_take = take_repository.get_in_pitch(take_id, pitch_id)

    if not existing_take:
        raise NonExistentTake()

    PitchRepository(db).clear_best_take(pitch_id, take_id)
    take_repository.delete(existing_take)
    db.commit()

    return take_id

def update_take_service(db: Session, pitch_id: uuid.UUID, take_id: uuid.UUID, take_update_dto: TakeUpdateRequestDTO):
    take_repository = TakeRepository(db)
    existing_take = take_repository.get_in_pitch(take_id, pitch_id)

    if not existing_take:
        raise NonExistentTake()

    existing_take.started_at = take_update_dto.started_at
    existing_take.ended_at = take_update_dto.ended_at
    existing_take.event_logs = take_update_dto.event_logs

    updated_take = take_repository.save(existing_take)
    db.commit()

    return updated_take.id

def delete_take_service(db: Session, pitch_id: uuid.UUID, take_id: uuid.UUID):
    take_repository = TakeRepository(db)
    existing_take = take_repository.get_in_pitch(take_id, pitch_id)

    if not existing_take:
        raise NonExistentTake()

    PitchRepository(db).clear_best_take(pitch_id, take_id)
    take_repository.delete(existing_take)
    db.commit()

    return take_id

# Calibration 완료 후 Calibration 데이터 저장
def create_calibration(db: Session, pitch_id: uuid.UUID, take_id: uuid.UUID, calibration_dto: CalibrationDTO):
    take_repository = TakeRepository(db)
    existing_take = take_repository.get_in_pitch(take_id, pitch_id)

    if not existing_take:
        raise NonExistentTake()

    ## calibration result 로직
    ...

    calibration_data = Calibration(
        take_id=take_id,
        face_detected=calibration_dto.face_detected,
        mic_detected=calibration_dto.mic_detected,
        base_volume=calibration_dto.base_volume,
        gaze_confidence=calibration_dto.gaze_confidence
    )

    saved_calibration = take_repository.save_calibration(calibration_data)
    db.commit()

    return saved_calibration.id

def get_previous_missions_service(db: Session, pitch_id: uuid.UUID):
    take_repository = TakeRepository(db)
    latest_take = take_repository.get_latest_take_in_pitch(pitch_id)

    if latest_take is None:
        return None

    return PreviousMissionsDTO(
        source_take_id=latest_take.id,
        next_take_number=latest_take.take_number + 1,
        missions = [
            MissionDTO(
                mission_id=mission.source_take_id,
                slide_number=mission.slide_number,
                description=mission.description,
                priority=mission.priority,
                completed=mission.complete
            ) for mission in take_repository.get_missions_in_take(latest_take.id)
        ]
    )



