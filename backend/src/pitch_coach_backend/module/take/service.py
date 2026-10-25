import asyncio
import logging
import math
import time
import uuid
from collections.abc import Callable, Iterable, Mapping
from datetime import datetime, timedelta
from typing import Any

import httpx
import httpx2
from psycopg.errors import ForeignKeyViolation, UniqueViolation
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from pitch_coach_backend.core.config import settings
from pitch_coach_backend.core.database import SessionLocal
from pitch_coach_backend.module.pitch import service as pitch_service
from pitch_coach_backend.module.pitch.entity import ScriptParseStatus
from pitch_coach_backend.module.pitch.exception import (
    NonExistentPitch,
    NonExistentPresentationVersion,
    NonExistentScript,
    ScriptNotParsed,
    SlideCountMismatch,
)
from pitch_coach_backend.module.pitch.repository import PitchRepository
from pitch_coach_backend.module.take import coach_service
from pitch_coach_backend.module.take.ai_response import (
    CoverageCallError,
    validate_coverage_reply,
    validate_review_reply,
)
from pitch_coach_backend.module.take.dto import (
    CalibrationDTO,
    MetricSecondCreateDTO,
    MissionInputDTO,
    PreviousTakeDTO,
    ReviewRequestDTO,
    ReviewResponseDTO,
    TakeHistoryResponseDTO,
    TakeHistoryRowDTO,
    TakeInitRequestDTO,
    TakeUpdateRequestDTO,
    TargetInfoDTO,
    TranscriptSegmentCreateDTO,
    UserCriteriaDTO,
)
from pitch_coach_backend.module.take.entity import Calibration, Take, TakeTranscriptSegment
from pitch_coach_backend.module.take.exception import (
    DuplicateTranscriptSegment,
    InvalidReviewResponse,
    InvalidTakeTime,
    NonExistentTake,
    TakeAlreadyEnded,
    TakeAlreadyStarted,
    TakeNotReviewable,
    TakeNotStarted,
    TakeSessionMismatch,
)
from pitch_coach_backend.module.take.repository import (
    IN_PROGRESS_STATUSES,
    TakeRepository,
    last_active_at,
)

logger = logging.getLogger(__name__)


# Take 생성, 삭제, 업데이트 서비스 함수들 정의.
def create_take_service(db: Session, pitch_id: uuid.UUID, take_dto: TakeInitRequestDTO):
    take_repository = TakeRepository(db)
    pitch_repository = PitchRepository(db)

    # 버전 ID 가 이 pitch 의 것인지 확인한다. 없으면 다른 pitch 의 버전도 저장되고,
    # 아예 없는 ID 는 FK 위반(500)이 된다
    presentation = pitch_repository.get_presentation_detail(
        pitch_id, take_dto.presentation_version_id
    )
    if presentation is None:
        raise NonExistentPresentationVersion()
    script = pitch_repository.get_script_in_pitch(pitch_id, take_dto.script_version_id)
    if script is None:
        raise NonExistentScript()
    # 슬라이드가 없으면 리허설이 대본을 띄울 수 없고 코치 · 분석도 장별로 나눌 수 없다
    if script.parse_status != ScriptParseStatus.DONE:
        raise ScriptNotParsed()
    # 장마다 슬라이드 한 쪽을 띄운다. 쪽수를 아직 모르는 옛 발표자료는 검사하지 않는다
    if (
        presentation.page_count is not None
        and presentation.page_count != pitch_repository.count_slides(script.id)
    ):
        raise SlideCountMismatch()

    # 동시에 두 요청이 같은 max+1 을 받지 않게 한 줄로 세운다 (발표자료·대본 버전과 같은 잠금).
    # 같은 세션으로 동시에 온 재시도도 여기서 줄을 서서 뒤의 요청이 앞의 Take 를 찾는다
    pitch_repository.lock_for_new_version(pitch_id)
    if take_dto.client_session_id is not None:
        existing_take = take_repository.get_by_client_session(
            pitch_id, take_dto.client_session_id
        )
        if existing_take is not None:
            if (
                existing_take.mode,
                existing_take.script_mode,
                existing_take.presentation_version_id,
                existing_take.script_version_id,
            ) != (
                take_dto.mode,
                take_dto.script_mode,
                take_dto.presentation_version_id,
                take_dto.script_version_id,
            ):
                raise TakeSessionMismatch()
            # 그사이 다른 Take 를 만들어 ABANDONED 가 됐거나 이미 끝났다. 돌려줘도 WS 가
            # TAKE_ENDED 로 막으니 FE 가 새 세션 id 로 다시 만들게 한다
            if existing_take.status not in IN_PROGRESS_STATUSES:
                raise TakeAlreadyEnded()
            return existing_take

    pitch = pitch_repository.get_by_id(pitch_id)
    if pitch is None:
        raise NonExistentPitch()

    take_repository.abandon_ready_takes(pitch_id)

    # 미션은 Take 를 시작할 때(첫 WS 연결) 싣는다 (`coach_service.start_take`). 만들 때 실으면 장치
    # 점검만 하고 버린 Take 가 미션을 가져가 다음 Take 에 실리지 않는다
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

    # 실시간 연결이 한 번도 붙지 않은 Take 는 끝내지 않는다. FE 는 첫 `ready` 를 받은 뒤에
    # 발표를 시작하므로 남은 기록이 없다. 만든 지 10분이 지나면 BE 가 ABANDONED 로 끝낸다
    if existing_take.status == "READY" and "ended_at" in changes:
        raise TakeNotStarted()

    # 떠난 것으로 끝낸 Take 에 온 끝내기다. 재연결을 포기하고 STT 없이 발표를 이어 간 FE 라
    # 기록을 받는다. 상태는 뒤이어 mark_ended 가 ANALYZING 으로 되돌린다
    abandoned_end = existing_take.end_reason == "ABANDONED" and "ended_at" in changes

    # 끝난 Take 는 분석이 읽고 있거나 이미 읽었다. ANALYZING 일 때 시각만 받는다 — FE 는
    # WS stop 으로 Take 를 끝낸 뒤 시각을 보내고, 종료 중 새로고침하면 같은 값을 다시 보낸다
    if (
        not abandoned_end
        and existing_take.status not in IN_PROGRESS_STATUSES
        and (existing_take.status != "ANALYZING" or "event_logs" in changes)
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

def close_take_service(db: Session, take_id: uuid.UUID) -> tuple[str, bool]:
    """창을 닫았다 다시 연 FE 가 Take 를 실패(`FAILED` · `CLOSED`)로 끝낸다. (지금 상태, 바꿨나).

    창을 닫은 Take 는 이어 가지 않는다. 진행 중(`READY` · `RUNNING`)일 때만 바꾸고, 이미 끝났으면
    그대로 두고 지금 상태를 돌려준다 — FE 는 `ANALYZING` 이면 끝내기가 이미 닿은 것으로 본다.
    끝난 시각은 마지막 활동 시각이다 (앱이 다시 뜰 때 떠난 Take 를 재는 기준과 같다).
    """
    take_repository = TakeRepository(db)
    closed = take_repository.mark_abandoned(take_id, last_active_at(), end_reason="CLOSED")
    db.commit()
    take = take_repository.get_take(take_id)
    if take is None:
        raise NonExistentTake()
    return take.status, closed


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

def _percent_to_ratio(value: int | None) -> float | None:
    # take_summaries 는 비율 × 100 정수로 둔다 (architecture.md 1-1 반올림)
    return None if value is None else value / 100


def _achieved_mission_count(mission_ids: Any, results: Any) -> int:
    """그 Take 에 고정된 미션 중 저장 리뷰가 달성으로 판정한 수. 같은 id 는 한 번만 센다."""
    if not isinstance(mission_ids, list) or not isinstance(results, list):
        return 0
    fixed_ids = {mission_id for mission_id in mission_ids if isinstance(mission_id, str)}
    return len({
        result["mission_id"]
        for result in results
        if isinstance(result, dict)
        and result.get("status") == "ACHIEVED"
        and isinstance(result.get("mission_id"), str)
        and result["mission_id"] in fixed_ids
    })


def list_takes_service(
    db: Session, user_id: uuid.UUID, pitch_id: uuid.UUID | None = None
) -> TakeHistoryResponseDTO:
    """Take 기록 · Take 비교 화면의 Take 목록. 남의 발표나 없는 발표를 주면 404 다."""
    if pitch_id is not None and PitchRepository(db).get_owned(pitch_id, user_id) is None:
        raise NonExistentPitch()
    rows = TakeRepository(db).list_started_takes(user_id, pitch_id)
    return TakeHistoryResponseDTO(
        takes=[
            TakeHistoryRowDTO(
                take_id=row.take_id,
                pitch_id=row.pitch_id,
                pitch_title=row.pitch_title,
                take_number=row.take_number,
                started_at=row.started_at,
                status=row.status,
                script_mode=row.script_mode,
                duration_ms=None if row.duration_sec is None else row.duration_sec * 1000,
                goal_ms=row.goal_time_sec * 1000,
                audience_ratio=_percent_to_ratio(row.audience_gaze),
                script_ratio=_percent_to_ratio(row.script_gaze),
                cpm=row.average_cpm,
                filler_count=row.filler_count,
                missions_achieved=_achieved_mission_count(row.mission_ids, row.mission_results),
                missions_total=row.missions_total,
                gaze_engine=row.gaze_engine,
            )
            for row in rows
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
        final_at_ms=segment_dto.final_at_ms,
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


def append_metric_seconds(
    db: Session, take_id: uuid.UUID, rows: list[MetricSecondCreateDTO]
) -> None:
    """1초 원자료 여러 행을 한 번에 저장하고 커밋한다. 이미 있는 1초는 건너뛴다.

    Take 가 그 사이 지워졌으면 `NonExistentTake`. 스트림은 이걸 보고 남은 행을 버린다.
    Take 결과를 이미 만든 Take 면 넣지 않는다 (`TakeRepository.raw_data_open`).
    """
    repository = TakeRepository(db)
    open_ = repository.raw_data_open(take_id)
    if open_ is None:
        raise NonExistentTake()
    if not open_:
        db.rollback()
        logger.warning(
            "Take 결과를 만든 뒤라 1초 원자료를 넣지 않는다 take=%s count=%d", take_id, len(rows)
        )
        return
    try:
        repository.save_metric_seconds(take_id, [row.model_dump() for row in rows])
    except IntegrityError as e:
        # 이 표의 외래 키는 take_id 하나다. 같은 1초는 ON CONFLICT 로 건너뛰어 여기 오지 않는다
        if isinstance(e.orig, ForeignKeyViolation):
            raise NonExistentTake() from e
        raise
    db.commit()

def _build_missions(take_repository: TakeRepository, take_id: uuid.UUID) -> list[MissionInputDTO]:
    mission_ids = take_repository.get_mission_ids_of_take(take_id)
    missions = take_repository.get_missions_by_ids(mission_ids)
    if len(missions) < len(mission_ids):
        logger.warning(
            "take %s: %d of %d missions not found",
            take_id, len(mission_ids) - len(missions), len(mission_ids),
        )
    return [
        MissionInputDTO(
            mission_id=mission.id,
            area=mission.area,
            slide_number=mission.slide_number,
            target=TargetInfoDTO(**mission.target),
        )
        for mission in missions
    ]

def _build_previous_takes(
    take_repository: TakeRepository, take_id: uuid.UUID, take_number: int
) -> list[PreviousTakeDTO]:
    return [
        PreviousTakeDTO(
            take_result=result,
            take_scores=coverage.get("take_scores") if coverage else None,
        )
        for result, coverage in take_repository.get_previous_take_results(take_id, take_number)
    ]

def _build_previous_recurring_issues(
    take_repository: TakeRepository, take_id: uuid.UUID, take_number: int
) -> list[dict]:
    previous_review = take_repository.get_previous_review_payload(take_id, take_number)
    return previous_review.get("recurring_issues", []) if previous_review else []

def _build_user_criteria(db: Session, take: Take) -> UserCriteriaDTO | None:
    criteria = pitch_service.criteria_for_version(db, take.pitch_id, take.standard_version)
    return UserCriteriaDTO.model_validate(criteria) if criteria is not None else None

def build_review_request(db: Session, take_id: uuid.UUID) -> ReviewRequestDTO:
    take_repository = TakeRepository(db)
    take = take_repository.get_take(take_id)
    if take is None:
        raise NonExistentTake()
    take_number = take.take_number
    result = take_repository.get_take_infos(take_id, "TAKE_RESULT")
    coverage = take_repository.get_take_infos(take_id, "SCRIPT_COVERAGE")
    return ReviewRequestDTO(
        take_result=result,
        script_coverage=coverage,
        previous_takes=_build_previous_takes(take_repository, take_id, take_number),
        previous_recurring_issues=_build_previous_recurring_issues(take_repository, 
                                                                   take_id, take_number),
        missions=_build_missions(take_repository, take_id),
        user_criteria=_build_user_criteria(db, take),
    )

REVIEW_RETRY_DELAY_SEC = 1.0


def request_review(take_id: uuid.UUID, review_request: ReviewRequestDTO) -> ReviewResponseDTO:
    """AI 서버에 리뷰를 요청한다. 응답이 계약과 다르거나 다른 Take 의 것이면
    InvalidReviewResponse(502). 저장하다 DB 제약에 걸리기 전에 여기서 거른다."""
    request_id = uuid.uuid4().hex
    with httpx2.Client(timeout=30.0) as client:
        for attempt in range(2):
            try:
                response = client.post(
                    f"{settings.ai_base_url}/takes/review",
                    json=review_request.model_dump(mode="json"),
                    headers={"X-Request-ID": request_id},
                )
            except httpx2.HTTPError as exc:
                if attempt:
                    raise InvalidReviewResponse() from exc
            else:
                if response.status_code == 200:
                    try:
                        reply = response.json()
                    except ValueError as exc:
                        raise InvalidReviewResponse() from exc
                    return validate_review_reply(reply, take_id)
                retryable = response.status_code >= 500 or response.status_code in (408, 429)
                if attempt or not retryable:
                    raise InvalidReviewResponse()
            time.sleep(REVIEW_RETRY_DELAY_SEC)
    raise AssertionError("unreachable")

def save_review(
    db: Session,
    take_id: uuid.UUID,
    review_data: ReviewResponseDTO,
    *,
    request_id: uuid.UUID | None = None,
) -> bool:
    review_data = validate_review_reply(review_data.model_dump(mode="json"), take_id)
    take_repository = TakeRepository(db)
    if request_id is None or take_repository.lock_review(take_id, request_id) is None:
        db.rollback()
        return False
    take_repository.save_total_result(
        take_id,
        review_data.model_dump(mode="json", exclude={"meta"}),
        review_data.meta.model_dump(mode="json"),
    )
    take_repository.upsert_next_missions(
        [
            {
                "source_take_id": take_id,
                "area": mission.area,
                "slide_number": mission.slide_number,
                "target": mission.target.model_dump(exclude={"display"}),
                "description": mission.description,
                "priority": mission.priority,
                "reason": mission.reason,
                "status": "ACTIVE",
            }
            for mission in review_data.next_missions
        ]
    )
    for result in review_data.mission_results:
        take_repository.update_mission_result(
            result.mission_id, take_id, result.status, result.observed
        )
    take_repository.upsert_summary_review(
        take_id, review_data.overall_summary, review_data.score
    )
    take_repository.set_analysis_stage(take_id, "DONE")
    take_repository.complete_take(take_id, "DONE")
    db.commit()
    return True


def _fail_review(db: Session, take_id: uuid.UUID, request_id: uuid.UUID) -> bool:
    repository = TakeRepository(db)
    if repository.lock_review(take_id, request_id) is None:
        db.rollback()
        return False
    repository.set_analysis_stage(take_id, "DONE")
    repository.complete_take(take_id, "FAILED")
    db.commit()
    return True


def get_review_result(db: Session, take_id: uuid.UUID) -> ReviewResponseDTO:
    take_repository = TakeRepository(db)
    request_id = uuid.uuid4()
    if not take_repository.start_review(take_id, request_id):
        db.rollback()
        raise TakeNotReviewable()
    db.commit()

    try:
        review_request = build_review_request(db, take_id)
        db.commit()
        review_data = request_review(take_id, review_request)
        if not save_review(db, take_id, review_data, request_id=request_id):
            raise TakeNotReviewable()
    except Exception:
        db.rollback()
        # 성공·실패 모두 현재 작업만 확정한다. 다른 작업이 이어받았거나 Take가 삭제됐으면 버린다.
        if not _fail_review(db, take_id, request_id):
            raise TakeNotReviewable() from None
        raise

    return review_data

def mark_running(db: Session, take_id: uuid.UUID) -> bool:
    """첫 실시간 연결이 붙었다. `READY` 면 `RUNNING` 으로 바꾸고, 이 Take 에 쓸 값(실은 미션 ·
    평가 기준 버전 · 원자료 사용 동의)을 고정한 뒤 커밋한다. 바꿨으면 True
    (`coach_service.start_take`).
    """
    return coach_service.start_take(db, take_id)


def mark_ended(
    db: Session,
    take_id: uuid.UUID,
    end_reason: str | None = None,
    utterance_ends: list[int] | None = None,
) -> bool:
    """Take 를 끝냈다. `RUNNING` 이면 상태를 `ANALYZING`, 분석 단계를 `FINALIZING` 으로 바꾸고
    커밋한다.

    WS stop 이 부르고, 발표 중에 연결이 끊겨 STT 없이 끝낸 Take 는 Take PUT(`ended_at`)이
    부른다. BE 가 자동으로 끝냈으면 `end_reason` 에 이유를 남긴다 (처음 적은 이유가 남는다).
    떠난 것으로 끝낸 Take 는 되살린다 (STT 없이 발표를 이어 간 FE 의 끝내기다).
    `utterance_ends` 는 Take 동안 메모리에 들고 있던 문장 끝 신호다 — 있으면 같이 쓴다
    (`docs/ai/architecture.md` 1-3 3번 ③).
    Take 가 그 사이 지워졌거나 이미 끝났으면 아무것도 안 하고 False.
    """
    marked = TakeRepository(db).mark_ended(take_id, end_reason, utterance_ends)
    db.commit()
    return marked


def mark_abandoned(
    db: Session,
    take_id: uuid.UUID,
    ended_at: datetime,
    end_reason: str = "ABANDONED",
    utterance_ends: list[int] | None = None,
) -> bool:
    """사용자가 떠난 Take 를 `FAILED` 로 바꾸고 이유(`ABANDONED` · `CLOSED`)를 적고 커밋한다.

    연결 없이 정리된 스트림에 정해진 시간 동안 다시 붙지 않으면 부른다. `ended_at` 은 마지막으로
    연결이 끊긴 시각이다. Take 동안 모은 문장 끝 신호가 있으면 같이 쓴다 — 끝내기 PUT 이 되살리면
    replay 가 쓴다. Take 가 그 사이 지워졌거나 이미 끝났으면 아무것도 안 하고 False.
    """
    marked = TakeRepository(db).mark_abandoned(take_id, ended_at, end_reason, utterance_ends)
    db.commit()
    return marked


def running_takes(db: Session) -> list[tuple[uuid.UUID, datetime]]:
    """`RUNNING` 인 Take 와 마지막으로 활동한 시각. 앱이 다시 뜰 때 떠난 Take 를 찾는다."""
    return TakeRepository(db).running_takes_last_active()


COVERAGE_KIND = "SCRIPT_COVERAGE"
COVERAGE_CANDIDATE_KIND = "SCRIPT_COVERAGE_PENDING"
COVERAGE_RETRYABLE_FAILED_CODES = frozenset(
    {"LLM_TIMEOUT", "LLM_ERROR", "LLM_INVALID_OUTPUT", "LLM_UNAVAILABLE"}
)


async def _post_ai(path: str, body: dict, request_id: str, timeout_sec: float) -> dict:
    try:
        async with asyncio.timeout(timeout_sec):
            async with httpx.AsyncClient(timeout=timeout_sec) as client:
                response = await client.post(
                    f"{settings.ai_base_url}{path}", json=body, headers={"X-Request-ID": request_id}
                )
    except (httpx.HTTPError, TimeoutError) as e:
        raise CoverageCallError(type(e).__name__, retryable=True) from e
    if response.status_code != 200:
        raise CoverageCallError(
            f"status={response.status_code}",
            retryable=response.status_code >= 500 or response.status_code in (408, 429),
        )
    try:
        reply = response.json()
    except ValueError as exc:
        raise CoverageCallError("invalid coverage JSON", retryable=False) from exc
    return validate_coverage_reply(reply, body["take_id"])


async def request_analyze(body: dict, request_id: str) -> dict:
    return await _post_ai("/takes/analyze", body, request_id, 130.0)


async def request_rescore(body: dict, request_id: str) -> dict:
    return await _post_ai("/takes/rescore", body, request_id, 10.0)


def slide_stt(
    slide_numbers: Iterable[int],
    slide_events: Iterable[tuple[int, int]],
    words: Iterable[tuple[str, int]],
) -> list[dict]:
    events = sorted(slide_events, key=lambda event: event[1])
    spoken: dict[int, list[str]] = {number: [] for number in sorted(slide_numbers)}
    for text, start_ms in sorted(words, key=lambda word: word[1]):
        current = None
        for slide_number, started_ms in events:
            if started_ms > start_ms:
                break
            current = slide_number
        if current in spoken and text:
            spoken[current].append(text)
    return [{"slide_number": number, "stt": " ".join(texts)} for number, texts in spoken.items()]


def retry_targets(failed: Iterable[Mapping[str, Any]]) -> list[int]:
    return sorted({
        item["slide_number"]
        for item in failed
        if (item.get("error") or {}).get("code") in COVERAGE_RETRYABLE_FAILED_CODES
    })


def merge_retry(
    base: Mapping[str, Any], retry: Mapping[str, Any], targets: Iterable[int]
) -> tuple[list[dict], list[dict]] | None:
    retried = set(targets)
    new_evaluations = [e for e in retry["evaluations"] if e["slide_number"] in retried]
    if not new_evaluations:
        return None
    succeeded = {e["slide_number"] for e in new_evaluations}
    evaluations = [e for e in base["evaluations"] if e["slide_number"] not in succeeded]
    failed = [f for f in base["failed"] if f["slide_number"] not in retried]
    failed += [f for f in retry["failed"] if f["slide_number"] in retried]
    return (
        sorted(evaluations + new_evaluations, key=lambda e: e["slide_number"]),
        sorted(failed, key=lambda f: f["slide_number"]),
    )


def speak_accuracy(payload: Mapping[str, Any]) -> float | None:
    value = (payload.get("take_scores") or {}).get("content_coverage")
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return math.ceil(value * 100)


def _coverage_open(take: Take | None) -> bool:
    return (
        take is not None
        and take.status == "ANALYZING"
        and take.analysis_stage == "SCORING"
        and take.analysis_hold_reason is None
    )


def _lock_open_take(take_repository: TakeRepository, take_id: uuid.UUID) -> Take | None:
    take = take_repository.lock_take(take_id)
    return take if _coverage_open(take) else None


def _mark_retry_used(db: Session, take_id: uuid.UUID, column: str) -> bool:
    take = _lock_open_take(TakeRepository(db), take_id)
    if take is None or getattr(take, column):
        db.rollback()
        return False
    setattr(take, column, True)
    db.commit()
    return True


def _save_confirmed(db: Session, take_id: uuid.UUID, reply: dict) -> dict | None:
    validate_coverage_reply(reply, take_id)
    take_repository = TakeRepository(db)
    payload = {key: value for key, value in reply.items() if key != "meta"}
    if _lock_open_take(take_repository, take_id) is None or not take_repository.insert_ai_result(
        take_id, COVERAGE_KIND, payload, reply.get("meta")
    ):
        db.rollback()
        return None
    take_repository.upsert_summary_speak_accuracy(take_id, speak_accuracy(payload))
    db.commit()
    return payload


def _save_candidate(db: Session, take_id: uuid.UUID, candidate: dict) -> bool:
    take_repository = TakeRepository(db)
    take = _lock_open_take(take_repository, take_id)
    confirmed = take_repository.get_ai_result(take_id, COVERAGE_KIND) if take else None
    if (
        confirmed is None
        or confirmed.revision != candidate["base_revision"]
        or str(take.script_version_id) != candidate["script_version_id"]
        or not take_repository.insert_ai_result(take_id, COVERAGE_CANDIDATE_KIND, candidate, None)
    ):
        db.rollback()
        return False
    db.commit()
    return True


def _apply_merged(db: Session, take_id: uuid.UUID, base_revision: int, reply: dict) -> bool:
    validate_coverage_reply(reply, take_id)
    take_repository = TakeRepository(db)
    payload = {key: value for key, value in reply.items() if key != "meta"}
    if _lock_open_take(take_repository, take_id) is None or not take_repository.replace_ai_result(
        take_id, COVERAGE_KIND, base_revision, payload, reply.get("meta")
    ):
        db.rollback()
        return False
    take_repository.upsert_summary_speak_accuracy(take_id, speak_accuracy(payload))
    take_repository.delete_ai_result(take_id, COVERAGE_CANDIDATE_KIND)
    db.commit()
    return True


def _hold_coverage(db: Session, take_id: uuid.UUID) -> None:
    take = _lock_open_take(TakeRepository(db), take_id)
    if take is None:
        db.rollback()
        return
    take.analysis_hold_reason = "COVERAGE_RESCORE_FAILED"
    db.commit()


def _conclude_coverage(db: Session, take_id: uuid.UUID) -> str:
    take_repository = TakeRepository(db)
    take = take_repository.lock_take(take_id)
    if take is None or take.status != "ANALYZING" or take.analysis_stage != "SCORING":
        db.rollback()
        return "SKIPPED"
    if take.analysis_hold_reason is not None or take_repository.get_ai_result(
        take_id, COVERAGE_CANDIDATE_KIND
    ):
        db.rollback()
        return "HELD"
    confirmed = take_repository.get_ai_result(take_id, COVERAGE_KIND)
    if confirmed is not None and confirmed.payload.get("confirmations"):
        take_repository.set_analysis_stage(take_id, "AWAITING_CONFIRMATION")
        db.commit()
        return "AWAITING_CONFIRMATION"
    take_repository.set_analysis_stage(take_id, "REVIEWING")
    db.commit()
    return "NO_COVERAGE" if confirmed is None else "READY_FOR_REVIEW"


async def _in_session[T](
    session_factory: Callable[[], Session], fn: Callable[..., T], *args: Any
) -> T:
    def call() -> T:
        db = session_factory()
        try:
            return fn(db, *args)
        finally:
            db.close()

    return await run_in_threadpool(call)


async def _merge_candidate(
    session_factory: Callable[[], Session], take_id: uuid.UUID, rubrics: list[dict],
    candidate: dict,
) -> None:
    body = {
        "take_id": str(take_id),
        "evaluations": candidate["evaluations"],
        "rubrics": rubrics,
        "answers": [],
        "failed": candidate["failed"],
    }
    request_id = uuid.uuid4().hex
    try:
        reply = await request_rescore(body, request_id)
    except CoverageCallError as e:
        logger.warning("take %s: coverage merge rescore failed: %s", take_id, e)
        if not e.retryable or not await _in_session(
            session_factory, _mark_retry_used, take_id, "coverage_rescore_retry_used"
        ):
            await _in_session(session_factory, _hold_coverage, take_id)
            return
        try:
            reply = await request_rescore(body, request_id)
        except CoverageCallError as retry_error:
            logger.warning("take %s: coverage merge rescore retry failed: %s", take_id, retry_error)
            await _in_session(session_factory, _hold_coverage, take_id)
            return
    if not await _in_session(
        session_factory, _apply_merged, take_id, candidate["base_revision"], reply
    ):
        logger.info("take %s: stale coverage merge result discarded", take_id)


async def _retry_failed_slides(
    session_factory: Callable[[], Session], take_id: uuid.UUID, body: dict,
    script_version_id: str, confirmed: dict, revision: int, retry_used: bool,
) -> None:
    targets = retry_targets(confirmed.get("failed", []))
    if not targets or retry_used or not await _in_session(
        session_factory, _mark_retry_used, take_id, "coverage_retry_used"
    ):
        return
    try:
        reply = await request_analyze({**body, "slide_numbers": targets}, uuid.uuid4().hex)
    except CoverageCallError as e:
        logger.warning("take %s: coverage partial retry failed: %s", take_id, e)
        return
    merged = merge_retry(confirmed, reply, targets)
    if merged is None:
        return
    candidate = {
        "script_version_id": script_version_id,
        "base_revision": revision,
        "evaluations": merged[0],
        "failed": merged[1],
        "retry_meta": reply.get("meta"),
    }
    if await _in_session(session_factory, _save_candidate, take_id, candidate):
        await _merge_candidate(session_factory, take_id, body["rubrics"], candidate)


async def _first_analyze(
    session_factory: Callable[[], Session],
    take_id: uuid.UUID,
    body: dict,
    script_version_id: str,
    retry_used: bool,
) -> None:
    # 전체 재시도를 보낸 뒤 확정본을 저장하기 전에 재시작했을 수 있다.
    # 이미 쓴 한도를 새 최초 호출로 취급하지 않는다.
    if retry_used:
        return
    request_id = uuid.uuid4().hex
    try:
        reply = await request_analyze(body, request_id)
    except CoverageCallError as e:
        logger.warning("take %s: coverage analyze failed: %s", take_id, e)
        if (
            not e.retryable
            or retry_used
            or not await _in_session(
                session_factory, _mark_retry_used, take_id, "coverage_retry_used"
            )
        ):
            return
        try:
            reply = await request_analyze(body, request_id)
        except CoverageCallError as retry_error:
            logger.warning(
                "take %s: coverage analyze retry failed: %s", take_id, retry_error
            )
            return
        await _in_session(session_factory, _save_confirmed, take_id, reply)
        return
    confirmed = await _in_session(session_factory, _save_confirmed, take_id, reply)
    if confirmed is not None:
        await _retry_failed_slides(
            session_factory, take_id, body, script_version_id, confirmed, 0, retry_used
        )


def _load_coverage(db: Session, take_id: uuid.UUID) -> dict | None:
    take_repository = TakeRepository(db)
    take = take_repository.get_take(take_id)
    if not _coverage_open(take):
        return None
    slides = PitchRepository(db).get_slides(take.script_version_id)
    confirmed = take_repository.get_ai_result(take_id, COVERAGE_KIND)
    candidate = take_repository.get_ai_result(take_id, COVERAGE_CANDIDATE_KIND)
    return {
        "body": {
            "take_id": str(take_id),
            "slides": slide_stt(
                [slide.slide_number for slide in slides],
                take_repository.get_slide_events(take_id),
                take_repository.get_transcript_words(take_id),
            ),
            "rubrics": [slide.rubric for slide in slides if slide.rubric is not None],
            "slide_numbers": None,
        },
        "script_version_id": str(take.script_version_id),
        "retry_used": take.coverage_retry_used,
        "rescore_retry_used": take.coverage_rescore_retry_used,
        "confirmed": (confirmed.payload, confirmed.revision) if confirmed else None,
        "candidate": candidate.payload if candidate else None,
    }


async def analyze_script_coverage(
    take_id: uuid.UUID, session_factory: Callable[[], Session] = SessionLocal
) -> str:
    job = await _in_session(session_factory, _load_coverage, take_id)
    if job is None:
        return "SKIPPED"
    body = job["body"]
    if job["candidate"] is not None:
        if job["rescore_retry_used"]:
            await _in_session(session_factory, _hold_coverage, take_id)
        else:
            await _merge_candidate(session_factory, take_id, body["rubrics"], job["candidate"])
    elif job["confirmed"] is None:
        await _first_analyze(
            session_factory, take_id, body, job["script_version_id"], job["retry_used"]
        )
    else:
        await _retry_failed_slides(
            session_factory, take_id, body, job["script_version_id"], *job["confirmed"],
            job["retry_used"],
        )
    return await _in_session(session_factory, _conclude_coverage, take_id)
ABANDON_AFTER_SEC = 5 * 60.0

def stored_utterance_ends(db: Session, take_id: uuid.UUID) -> list[int]:
    """Take 에 저장된 문장 끝 신호. 앱이 Take 도중에 다시 떴을 때 이어 받는다."""
    return TakeRepository(db).utterance_ends(take_id)

def save_utterance_ends(db: Session, take_id: uuid.UUID, utterance_ends: list[int]) -> bool:
    """진행 중인 Take 의 문장 끝 신호를 적어 둔다 (앱을 내릴 때). 끝난 Take 는 그대로 둔다."""
    saved = TakeRepository(db).save_utterance_ends(take_id, utterance_ends)
    db.commit()
    return saved


# 시작하지 않은 Take 를 끝내는 기준. Take 는 장치 점검에서 시작을 누를 때 만들고 곧바로 실시간
# 연결이 붙으므로 보통 몇 초면 `RUNNING` 이 된다. 권한 창 · 느린 연결을 넉넉히 기다린다
READY_EXPIRE_AFTER = timedelta(minutes=10)


def abandon_stale_ready_takes(db: Session) -> int:
    """만든 지 `READY_EXPIRE_AFTER` 가 지나도 `READY` 인 Take 를 `FAILED`(`ABANDONED`)로 끝내고
    커밋한다. 끝낸 수를 돌려준다."""
    abandoned = TakeRepository(db).abandon_stale_ready_takes(READY_EXPIRE_AFTER)
    db.commit()
    return abandoned
