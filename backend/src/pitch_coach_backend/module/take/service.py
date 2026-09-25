import uuid

from sqlalchemy.orm import Session

from pitch_coach_backend.module.pitch.repository import PitchRepository
from pitch_coach_backend.module.take.dto import CalibrationDTO, MissionDTO, PreviousMissionsDTO, TakeInitRequestDTO, TakeUpdateRequestDTO
from pitch_coach_backend.module.take.entity import Calibration, Take
from pitch_coach_backend.module.take.dto import (
    CalibrationDTO,
    TakeInitRequestDTO,
    TakeUpdateRequestDTO,
    TranscriptSegmentCreateDTO,
)
from pitch_coach_backend.module.take.entity import Calibration, Take, TakeTranscriptSegment
from pitch_coach_backend.module.take.exception import NonExistentTake
from pitch_coach_backend.module.take.repository import TakeRepository


# Take 생성, 삭제, 업데이트 서비스 함수들 정의.
def create_take_service(db: Session, pitch_id: uuid.UUID, take_dto: TakeInitRequestDTO):
    take_repository = TakeRepository(db)
    
    next_take_number = take_repository.next_take_number(pitch_id)
    new_take = Take(
        pitch_id=pitch_id,
        mode=take_dto.mode,
        script_mode=take_dto.script_mode,
        presentation_version_id=take_dto.presentation_version_id,
        script_version_id=take_dto.script_version_id,
        take_number=next_take_number
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
def create_calibration_service(db: Session, pitch_id: uuid.UUID, take_id: uuid.UUID, calibration_dto: CalibrationDTO):
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

def create_missions(db: Session, pitch_id: uuid.UUID, take_id: uuid.UUID):
    # 미션 생성 로직
    pass

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


# ── 실시간 STT (realtime 모듈이 부른다) ──────────────────────────────
#
# realtime 은 entity 를 갖지 않고 저장을 여기에 위임한다. 아래 셋은 WebSocket 연결·
# 백그라운드 스트림에서 불리므로 **호출자가 run_in_threadpool 로 감싼다** (동기 SQLAlchemy).


def find_owned(db: Session, take_id: uuid.UUID, user_id: uuid.UUID) -> Take | None:
    """없을 수 있는 조회. 남의 take 도 None — WebSocket 이 존재 여부를 흘리지 않게."""
    return TakeRepository(db).get_owned(take_id, user_id)


def transcript_cursor(db: Session, take_id: uuid.UUID) -> tuple[int, int]:
    """저장된 마지막 (seq, stt_session_no). 스트림이 새로 만들어질 때 번호를 이어 받는다."""
    return TakeRepository(db).last_transcript_cursor(take_id)


def append_transcript_segment(
    db: Session, take_id: uuid.UUID, segment_dto: TranscriptSegmentCreateDTO
) -> uuid.UUID:
    """final 구간 하나를 저장하고 커밋한다. 짧은 트랜잭션 하나로 끝난다."""
    segment = TakeTranscriptSegment(
        take_id=take_id,
        seq=segment_dto.seq,
        stt_session_no=segment_dto.stt_session_no,
        start_ms=segment_dto.start_ms,
        end_ms=segment_dto.end_ms,
        transcript=segment_dto.transcript,
        words=[w.model_dump() for w in segment_dto.words],
        confidence=segment_dto.confidence,
        speech_final=segment_dto.speech_final,
    )
    saved = TakeRepository(db).save_transcript_segment(segment)
    db.commit()
    return saved.id
