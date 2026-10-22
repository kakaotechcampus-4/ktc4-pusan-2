import uuid
from datetime import datetime

from psycopg.errors import ForeignKeyViolation, UniqueViolation
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from pitch_coach_backend.module.pitch.entity import ScriptParseStatus
from pitch_coach_backend.module.pitch.exception import (
    NonExistentPitch,
    NonExistentPresentationVersion,
    NonExistentScript,
    ScriptNotParsed,
)
from pitch_coach_backend.module.pitch.repository import PitchRepository
from pitch_coach_backend.module.take.dto import (
    CalibrationDTO,
    MissionDTO,
    PreviousMissionsDTO,
    TakeInitRequestDTO,
    TakeUpdateRequestDTO,
    TranscriptSegmentCreateDTO,
)
from pitch_coach_backend.module.take.entity import Calibration, Take, TakeTranscriptSegment
from pitch_coach_backend.module.take.exception import (
    DuplicateTranscriptSegment,
    InvalidTakeTime,
    NonExistentTake,
    TakeAlreadyEnded,
    TakeAlreadyStarted,
)
from pitch_coach_backend.module.take.repository import IN_PROGRESS_STATUSES, TakeRepository


# Take 생성, 삭제, 업데이트 서비스 함수들 정의.
def create_take_service(db: Session, pitch_id: uuid.UUID, take_dto: TakeInitRequestDTO):
    take_repository = TakeRepository(db)
    pitch_repository = PitchRepository(db)

    # 버전 ID 가 이 pitch 의 것인지 확인한다. 없으면 다른 pitch 의 버전도 저장되고,
    # 아예 없는 ID 는 FK 위반(500)이 된다
    if pitch_repository.get_presentation_detail(pitch_id, take_dto.presentation_version_id) is None:
        raise NonExistentPresentationVersion()
    script = pitch_repository.get_script_in_pitch(pitch_id, take_dto.script_version_id)
    if script is None:
        raise NonExistentScript()
    # 슬라이드가 없으면 리허설이 대본을 띄울 수 없고 코치 · 분석도 장별로 나눌 수 없다
    if script.parse_status != ScriptParseStatus.DONE:
        raise ScriptNotParsed()

    # 동시에 두 요청이 같은 max+1 을 받지 않게 한 줄로 세운다 (발표자료·대본 버전과 같은 잠금).
    # 같은 세션으로 동시에 온 재시도도 여기서 줄을 서서 뒤의 요청이 앞의 Take 를 찾는다
    pitch_repository.lock_for_new_version(pitch_id)
    if take_dto.client_session_id is not None:
        existing_take = take_repository.get_by_client_session(
            pitch_id, take_dto.client_session_id
        )
        if existing_take is not None:
            return existing_take

    pitch = pitch_repository.get_by_id(pitch_id)
    if pitch is None:
        raise NonExistentPitch()

    take_repository.abandon_ready_takes(pitch_id)
    next_take_number = take_repository.next_take_number(pitch_id)
    new_take = Take(
        pitch_id=pitch_id,
        mode=take_dto.mode,
        script_mode=take_dto.script_mode,
        presentation_version_id=take_dto.presentation_version_id,
        script_version_id=take_dto.script_version_id,
        take_number=next_take_number,
        goal_time_sec=pitch.time_limit_sec,
        client_session_id=take_dto.client_session_id,
    )

    saved_take = take_repository.save(new_take)
    db.commit()

    return saved_take

def update_take_service(db: Session, take_id: uuid.UUID, take_update_dto: TakeUpdateRequestDTO):
    take_repository = TakeRepository(db)
    existing_take = take_repository.get_take(take_id)

    if not existing_take:
        raise NonExistentTake()

    # 보낸 필드만 바꾼다. 빠진 필드를 None 으로 덮으면 ended_at 만 보낼 때 started_at 이 지워진다
    changes = take_update_dto.model_dump(exclude_none=True)

    # 끝난 Take 는 분석이 읽고 있거나 이미 읽었다. ANALYZING 일 때 시각만 받는다 — FE 는
    # WS stop 으로 Take 를 끝낸 뒤 시각을 보내고, 종료 중 새로고침하면 같은 값을 다시 보낸다
    if existing_take.status not in IN_PROGRESS_STATUSES and (
        existing_take.status != "ANALYZING" or "event_logs" in changes
    ):
        raise TakeAlreadyEnded()

    started_at = changes.get("started_at", existing_take.started_at)
    ended_at = changes.get("ended_at", existing_take.ended_at)
    if started_at is not None and ended_at is not None and ended_at < started_at:
        raise InvalidTakeTime()

    for field, value in changes.items():
        setattr(existing_take, field, value)

    updated_take = take_repository.save(existing_take)
    db.commit()

    return updated_take.id

def delete_take_service(db: Session, take_id: uuid.UUID):
    take_repository = TakeRepository(db)
    existing_take = take_repository.get_take(take_id)

    if not existing_take:
        raise NonExistentTake()

    take_repository.delete(existing_take)
    db.commit()

    return take_id

# Calibration 완료 후 Calibration 데이터 저장
def create_calibration_service(db: Session, take_id: uuid.UUID, calibration_dto: CalibrationDTO):
    take_repository = TakeRepository(db)
    existing_take = take_repository.get_take(take_id)

    if not existing_take:
        raise NonExistentTake()
    # 점검은 발표 전에 한다. 시작한 Take 의 기준값(평소 목소리 크기 등)을 바꾸면
    # 이미 잰 발표를 다른 기준으로 분석하게 된다
    if existing_take.status != "READY":
        raise TakeAlreadyStarted()

    ## calibration result 로직
    ...

    calibration_data = Calibration(
        take_id=take_id,
        base_level_db=calibration_dto.base_level_db,
        result=calibration_dto.result.model_dump() if calibration_dto.result else None,
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
        return PreviousMissionsDTO(source_take_id=None, next_take_number=1, missions=[])

    return PreviousMissionsDTO(
        source_take_id=latest_take.id,
        next_take_number=latest_take.take_number + 1,
        missions = [
            MissionDTO(
                mission_id=mission.id,
                slide_number=mission.slide_number,
                description=mission.description,
                priority=mission.priority,
                completed=mission.complete
            ) for mission in take_repository.get_missions_in_take(latest_take.id)
        ]
    )


# ── 실시간 STT (realtime 모듈이 부른다) ──────────────────────────────
#
# realtime 은 entity 를 갖지 않고 저장을 여기에 위임한다. 아래 일곱은 WebSocket 연결·
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
    """final 구간 하나를 저장하고 커밋한다. 짧은 트랜잭션 하나로 끝난다.

    Take 가 그 사이 지워졌으면 `NonExistentTake`. 스트림은 이걸 보고 더 돌지 않는다.
    같은 번호가 이미 있으면 `DuplicateTranscriptSegment`. 스트림은 저장된 것으로 보고 넘어간다.
    """
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
    try:
        saved = TakeRepository(db).save_transcript_segment(segment)
    except IntegrityError as e:
        # 이 표의 외래 키는 take_id 하나, UNIQUE 는 (take_id, seq) 하나다
        if isinstance(e.orig, ForeignKeyViolation):
            raise NonExistentTake() from e
        if isinstance(e.orig, UniqueViolation):
            raise DuplicateTranscriptSegment() from e
        raise
    db.commit()
    return saved.id


def mark_running(db: Session, take_id: uuid.UUID) -> bool:
    """첫 실시간 연결이 붙었다. `READY` 면 `RUNNING` 으로 바꾸고 커밋한다. 바꿨으면 True."""
    marked = TakeRepository(db).mark_running(take_id)
    db.commit()
    return marked


def mark_ended(db: Session, take_id: uuid.UUID, end_reason: str | None = None) -> bool:
    """Take 를 끝냈다. 상태를 `ANALYZING` 으로 바꾸고 커밋한다.

    WS stop 이 부르고, STT 없이 끝낸 Take 는 Take PUT(`ended_at`)이 부른다. BE 가 자동으로
    끝냈으면 `end_reason` 에 이유를 남긴다 (처음 적은 이유가 남는다).
    Take 가 그 사이 지워졌거나 이미 끝났으면 아무것도 안 하고 False.
    """
    marked = TakeRepository(db).mark_ended(take_id, end_reason)
    db.commit()
    return marked


def mark_abandoned(db: Session, take_id: uuid.UUID, ended_at: datetime) -> bool:
    """사용자가 떠난 Take 를 `FAILED`(`ABANDONED`)로 바꾸고 커밋한다.

    연결 없이 정리된 스트림에 정해진 시간 동안 다시 붙지 않으면 부른다. `ended_at` 은 마지막으로
    연결이 끊긴 시각이다. Take 가 그 사이 지워졌거나 이미 끝났으면 아무것도 안 하고 False.
    """
    marked = TakeRepository(db).mark_abandoned(take_id, ended_at)
    db.commit()
    return marked


def running_takes(db: Session) -> list[tuple[uuid.UUID, datetime]]:
    """`RUNNING` 인 Take 와 마지막으로 활동한 시각. 앱이 다시 뜰 때 떠난 Take 를 찾는다."""
    return TakeRepository(db).running_takes_last_active()
