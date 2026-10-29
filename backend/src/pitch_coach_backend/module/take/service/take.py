"""Take 생성 · 수정 · 삭제 · 소유권 조회 · 장치 점검 · Take 목록 · 리포트."""

import uuid
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from pitch_coach_backend.module.pitch.entity import ScriptParseStatus
from pitch_coach_backend.module.pitch.exception import (
    NonExistentPitch,
    NonExistentPresentationVersion,
    NonExistentScript,
    ScriptNotParsed,
    SlideCountMismatch,
)
from pitch_coach_backend.module.pitch.repository import PitchRepository
from pitch_coach_backend.module.take.dto import (
    ActiveTakeDTO,
    ActiveTakeResponseDTO,
    CalibrationDTO,
    TakeHistoryResponseDTO,
    TakeHistoryRowDTO,
    TakeInitRequestDTO,
    TakeMeasuresDTO,
    TakeReportDTO,
    TakeUpdateRequestDTO,
)
from pitch_coach_backend.module.take.entity import Calibration, Take
from pitch_coach_backend.module.take.exception import (
    InvalidTakeTime,
    NonExistentTake,
    TakeAlreadyEnded,
    TakeAlreadyStarted,
    TakeNotStarted,
    TakeSessionMismatch,
    TakeTimeOutOfRange,
)
from pitch_coach_backend.module.take.measures import take_count, take_metric
from pitch_coach_backend.module.take.repository import IN_PROGRESS_STATUSES, TakeRepository

# 시작 · 끝 시각으로 받는 범위의 여유. Take 를 만든 때보다 이만큼 앞부터 지금보다 이만큼 뒤까지
# 받는다. FE 시계로 잰 값이라 서버 시계와 어긋날 수 있다. 앞뒤로 같은 여유를 둔다 — 끝내기 PUT 이
# 거절되면 FE 는 같은 값으로 계속 다시 보내서 시계가 빠른 브라우저는 Take 를 끝내지 못한다
TAKE_TIME_TOLERANCE = timedelta(days=1)


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

    # 0001년 같은 값은 UTC 로 바꾸면 날짜 범위를 벗어나 500 이 되고, 관리자 연습 시간도 망가진다
    earliest = existing_take.created_at - TAKE_TIME_TOLERANCE
    latest = datetime.now(UTC) + TAKE_TIME_TOLERANCE
    for field in ("started_at", "ended_at"):
        if field in changes and not earliest <= changes[field] <= latest:
            raise TakeTimeOutOfRange()

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
    # 같은 Take 를 시작하는 요청(`coach_service.start_take`)과 줄을 세운다. 검사와 저장 사이에
    # Take 가 시작되면 코치는 옛 기준값으로 돌고 DB 에는 새 값이 남는다. 발표 행은 잠그지 않아
    # 시작 쪽의 발표 → Take 잠금 순서와 엇갈리지 않는다
    existing_take = take_repository.lock_take(take_id)

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
                end_reason=row.end_reason,
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


def active_take_service(
    db: Session, user_id: uuid.UUID, take_id: uuid.UUID | None
) -> ActiveTakeResponseDTO:
    """발표 중인 Take(`take_id`, 실시간 연결이 알려 준 것)의 발표 제목 · 번호. 그 사이 끝났거나
    지워졌으면 없는 것으로 답한다."""
    owned = (
        TakeRepository(db).get_owned_with_pitch_title(take_id, user_id)
        if take_id is not None
        else None
    )
    if owned is None or owned[0].status not in IN_PROGRESS_STATUSES:
        return ActiveTakeResponseDTO(take=None)
    take, pitch_title = owned
    return ActiveTakeResponseDTO(
        take=ActiveTakeDTO(
            take_id=take.id,
            pitch_id=take.pitch_id,
            pitch_title=pitch_title,
            take_number=take.take_number,
        )
    )


def _take_measures(take: Take, take_result: Mapping[str, Any] | None) -> TakeMeasuresDTO | None:
    if take_result is None:
        return None
    areas = take_result.get("areas") or {}
    return TakeMeasuresDTO(
        duration_ms=take_result["duration_ms"],
        goal_ms=take.goal_time_sec * 1000,
        audience_ratio=take_metric(areas, "GAZE", "audience_ratio"),
        screen_ratio=take_metric(areas, "GAZE", "screen_ratio"),
        script_ratio=take_metric(areas, "GAZE", "script_ratio"),
        away_ratio=take_metric(areas, "GAZE", "away_ratio"),
        unmeasured_ms=None,
        cpm=take_metric(areas, "SPEED", "cpm"),
        filler_count=take_count(areas, "FILLER", "filler_count"),
    )


def get_take_report_service(
    db: Session, take_id: uuid.UUID, user_id: uuid.UUID
) -> TakeReportDTO:
    take_repository = TakeRepository(db)
    owned = take_repository.get_owned_with_pitch_title(take_id, user_id)
    if owned is None:
        raise NonExistentTake()
    take, pitch_title = owned
    payloads = take_repository.get_ai_payloads(take_id, ("TAKE_RESULT", "REVIEW"))
    return TakeReportDTO(
        take_id=take.id,
        pitch_id=take.pitch_id,
        pitch_title=pitch_title,
        take_number=take.take_number,
        script_mode=take.script_mode,
        ended_at=take.ended_at,
        status=take.status,
        analysis_stage=take.analysis_stage,
        review_status=take.review_status,
        analysis_hold_reason=take.analysis_hold_reason,
        measures=_take_measures(take, payloads.get("TAKE_RESULT")),
        review=payloads.get("REVIEW"),
    )


# ── 실시간 STT (realtime 모듈이 부른다) ──────────────────────────────
#
# realtime 은 entity 를 갖지 않고 저장을 여기에 위임한다. 아래 일곱은 WebSocket 연결·
# 백그라운드 스트림에서 불리므로 **호출자가 run_in_threadpool 로 감싼다** (동기 SQLAlchemy).
def find_owned(db: Session, take_id: uuid.UUID, user_id: uuid.UUID) -> Take | None:
    """없을 수 있는 조회. 남의 take 도 None — WebSocket 이 존재 여부를 흘리지 않게."""
    return TakeRepository(db).get_owned(take_id, user_id)


def has_completed_report(db: Session, pitch_ids: list[uuid.UUID]) -> bool:
    """본인 발표 범위에 완료된 측정 결과가 하나라도 남아 있는가."""
    return TakeRepository(db).has_completed_report(pitch_ids)
