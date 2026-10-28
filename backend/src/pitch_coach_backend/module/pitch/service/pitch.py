"""발표 생성 · 목록 · 자료 모음 · 준비 화면 · 수정 · 삭제.

다른 도메인과 코치가 쓰는 발표 조회 · 코칭 계획 행도 여기 둔다.
"""

import logging
import uuid
from collections import defaultdict
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta, timezone

from sqlalchemy.orm import Session

from pitch_coach_backend.module.pitch.adapter.storage import delete_prefix
from pitch_coach_backend.module.pitch.dto import (
    NewerScriptDTO,
    ParsedScriptDTO,
    PitchListItemDTO,
    PitchListResponseDTO,
    PitchSaveRequestDTO,
    PrepareDTO,
    PrepareMissionDTO,
    PrepareMissionsDTO,
    PrepareMissionTargetDTO,
    PresentationVersionDTO,
    VersionDTO,
    Versioned,
    VersionSummaryDTO,
)
from pitch_coach_backend.module.pitch.entity import CoachingPlan, Pitch, PresentationVersion
from pitch_coach_backend.module.pitch.exception import NonExistentPitch
from pitch_coach_backend.module.pitch.repository import PitchRepository
from pitch_coach_backend.module.pitch.service.script import effective_status
from pitch_coach_backend.module.take.coach_repository import CoachRepository
from pitch_coach_backend.module.take.dto import TakeSummaryDTO
from pitch_coach_backend.module.take.repository import TakeRepository

logger = logging.getLogger(__name__)


# 발표일은 사용자가 고른 한국 날짜다. 오늘도 한국 날짜로 봐야 0~9시에 오늘 발표가 지난 발표가
# 되지 않는다. 관리자 콘솔(admin/repository.py)과 같은 고정 오프셋이다
KST = timezone(timedelta(hours=9), "KST")


def add_pitch_service(db: Session, user_id: uuid.UUID, pitch_dto: PitchSaveRequestDTO):
    new_pitch = Pitch(
        user_id=user_id,
        title=pitch_dto.title,
        time_limit_sec=pitch_dto.time_limit_sec,
        presentation_date=pitch_dto.presentation_date,
        upper_deviation=pitch_dto.upper_deviation,
        lower_deviation=pitch_dto.lower_deviation
    )

    pitch_repository = PitchRepository(db)
    saved_pitch = pitch_repository.save(new_pitch)
    db.commit()

    return saved_pitch.id


# 홈 화면 : pitches 목록 조회
def get_all_pitches_service(db: Session, user_id: uuid.UUID) -> PitchListResponseDTO:
    pitch_repository = PitchRepository(db)
    pitches = pitch_repository.get_all_by_user(user_id, datetime.now(KST).date())

    rows_by_pitch = defaultdict(list)
    for row in pitch_repository.get_takes_with_scores_in_pitches([p.id for p in pitches]):
        rows_by_pitch[row.Take.pitch_id].append(row)

    results = []
    for pitch in pitches:
        take_summaries = []
        # Take, Score로 이루어진 row.
        takes = rows_by_pitch[pitch.id]

        for t in range(len(takes)):
            take = takes[t].Take
            take_score = takes[t].score
            d = None

            if t > 0:
                previous_take_score = takes[t - 1].score
                if take_score is not None and previous_take_score is not None:
                    d = take_score - previous_take_score

            take_summary = TakeSummaryDTO(
                take_id=take.id,
                status=take.status,
                end_reason=take.end_reason,
                take_version=take.take_number,
                take_elapsed=take.duration_sec,
                take_time=take.goal_time_sec,
                script_mode=take.script_mode,
                score=take_score,
                delta=d,
                created_at = take.created_at
            )

            take_summaries.append(take_summary)

        pitch_dto = PitchListItemDTO(
            pitch_id=pitch.id,
            pitch_title=pitch.title,
            pitch_time=pitch.time_limit_sec,
            thumbnail_url=None,
            presentation_date=pitch.presentation_date,
            takes=take_summaries
        )

        results.append(pitch_dto)

    return PitchListResponseDTO(pitches=results)


# Protocol(해당 타입만 가지고 있다면 Versioned 타입으로 간주)로 통일
# Versioned 타입을 가진 객체들을 VersionDTO로 변환
def to_version_dtos(rows: Iterable[Versioned]) -> list[VersionDTO]:
    return [VersionDTO(id=row.id, version=row.version) for row in rows]


# 발표 자료 버전 들고오기
def get_pitch_datas(db: Session, pitch_id: uuid.UUID):
    pitch_repository = PitchRepository(db)

    return VersionSummaryDTO(
        pitch_id=pitch_id,
        presentation_versions=[
            _presentation_dto(row) for row in pitch_repository.get_presentations(pitch_id)
        ],
        script_versions=to_version_dtos(pitch_repository.get_scripts(pitch_id)),
        evaluation_versions=to_version_dtos(pitch_repository.get_evaluations(pitch_id)),
    )


def _presentation_dto(row: PresentationVersion) -> PresentationVersionDTO:
    return PresentationVersionDTO(id=row.id, version=row.version, page_count=row.page_count)


def _prepare_missions(db: Session, pitch_id: uuid.UUID) -> PrepareMissionsDTO | None:
    """Take 를 시작할 때 싣는 미션(`CoachRepository.assign_missions`)과 같은 Take · 순서로 고른다.

    지금 있는 Take 는 모두 다음 번호보다 작아서 `before` 없이 고른 것과 같다.
    """
    source = TakeRepository(db).get_latest_reviewed_take_in_pitch(pitch_id)
    if source is None:
        return None
    return PrepareMissionsDTO(
        source_take_number=source.take_number,
        items=[
            PrepareMissionDTO(
                mission_id=mission.id,
                priority=mission.priority,
                area=mission.area,
                slide_number=mission.slide_number,
                description=mission.description,
                target=PrepareMissionTargetDTO.model_validate(mission.target),
                reason=mission.reason,
            )
            for mission in CoachRepository(db).active_missions_from(source.id)
        ],
    )


# 장치 점검 화면. 홈에서 바로 들어오거나 새로고침해도 Take 에 넣을 버전을 알 수 있게 한다
def get_prepare_service(db: Session, pitch_id: uuid.UUID) -> PrepareDTO:
    pitch_repository = PitchRepository(db)
    pitch = pitch_repository.get_by_id(pitch_id)

    if not pitch:
        raise NonExistentPitch()

    script = pitch_repository.get_latest_parsed_script(pitch_id)
    slide_count = pitch_repository.count_slides(script.id) if script else None
    # FE 매핑 확인과 같은 규칙이다. 장 수와 쪽수가 같은 가장 최근 발표자료, 없으면 가장 최근 것
    presentation = None
    if slide_count:
        presentation = pitch_repository.get_latest_presentation(pitch_id, page_count=slide_count)
    if presentation is None:
        presentation = pitch_repository.get_latest_presentation(pitch_id)
    latest_script = pitch_repository.get_latest_script(pitch_id)
    latest_take = TakeRepository(db).get_latest_take_in_pitch(pitch_id)

    newer_script = None
    if latest_script is not None and (script is None or latest_script.version > script.version):
        status, error = effective_status(latest_script, datetime.now(UTC))
        newer_script = NewerScriptDTO(
            id=latest_script.id,
            version=latest_script.version,
            parse_status=status,
            error_code=error,
        )

    return PrepareDTO(
        pitch_id=pitch.id,
        title=pitch.title,
        time_limit_sec=pitch.time_limit_sec,
        script=(
            ParsedScriptDTO(id=script.id, version=script.version, slide_count=slide_count or 0)
            if script
            else None
        ),
        presentation=_presentation_dto(presentation) if presentation else None,
        newer_script=newer_script,
        next_take_number=latest_take.take_number + 1 if latest_take else 1,
        default_script_mode=latest_take.script_mode if latest_take else "HIGHLIGHT",
        missions=_prepare_missions(db, pitch_id),
    )


def update_pitch_service(db: Session, pitch_id: uuid.UUID, pitch_dto):
    pitch_repository = PitchRepository(db)
    existing_pitch = pitch_repository.get_by_id(pitch_id)

    if not existing_pitch:
        raise NonExistentPitch()

    existing_pitch.title = pitch_dto.title
    existing_pitch.time_limit_sec = pitch_dto.time_limit_sec
    existing_pitch.presentation_date = pitch_dto.presentation_date
    existing_pitch.upper_deviation = pitch_dto.upper_deviation
    existing_pitch.lower_deviation = pitch_dto.lower_deviation

    updated_pitch = pitch_repository.save(existing_pitch)
    db.commit()

    return updated_pitch.id


def delete_pitch_service(db: Session, pitch_id: uuid.UUID) -> list[uuid.UUID]:
    """pitch 를 지운다. Take 와 그 아래 행은 CASCADE 로 같이 지워진다.

    지운 Take 의 id 를 돌려준다. 진행 중이던 Take 의 실시간 STT 는 호출자가 끊는다.
    """
    pitch_repository = PitchRepository(db)
    existing_pitch = pitch_repository.get_by_id(pitch_id)

    if not existing_pitch:
        raise NonExistentPitch()

    # 지운 뒤에는 CASCADE 로 사라져 읽을 수 없다
    take_ids = pitch_repository.get_take_ids(pitch_id)
    pitch_repository.delete(existing_pitch)
    db.commit()

    return take_ids


def delete_pitch_files(pitch_id: uuid.UUID) -> None:
    """S3 에 올린 이 pitch 의 파일을 지운다. pitch 행을 지운 커밋 뒤 백그라운드로 돈다.

    실패해도 삭제 요청은 이미 성공했다. 남은 파일은 로그의 pitch_id 로 찾아 지운다.
    """
    try:
        delete_prefix(f"pitches/{pitch_id}/")
    except Exception:
        logger.exception("pitch 파일 삭제 실패 pitch_id=%s", pitch_id)


def get_pitch_ids_of_user(db: Session, user_id: uuid.UUID) -> list[uuid.UUID]:
    return PitchRepository(db).get_ids_by_user(user_id)


def get_take_ids_of_user(db: Session, user_id: uuid.UUID) -> list[uuid.UUID]:
    return PitchRepository(db).get_take_ids_by_user(user_id)


def lock_for_new_version(db: Session, pitch_id: uuid.UUID) -> None:
    """발표 행을 잠근다 (`PitchRepository.lock_for_new_version`). Take 를 시작할 때도 잡는다."""
    PitchRepository(db).lock_for_new_version(pitch_id)


def find_pitch(db: Session, pitch_id: uuid.UUID) -> Pitch | None:
    return PitchRepository(db).get_by_id(pitch_id)


def chosen_coaching_plan(
    db: Session,
    pitch_id: uuid.UUID,
    *,
    source_take_id: uuid.UUID | None,
    script_version_id: uuid.UUID | None,
    target_ms: int | None,
    min_ms: int | None,
    max_ms: int | None) -> CoachingPlan | None:
    """바탕 값이 모두 같은 계획 중 요청 시각이 가장 늦은 것 (coach-agent.md 12-2 계획 고르기)."""
    return PitchRepository(db).get_chosen_coaching_plan(
        pitch_id,
        source_take_id=source_take_id,
        script_version_id=script_version_id,
        target_ms=target_ms,
        min_ms=min_ms,
        max_ms=max_ms,
    )


def add_coaching_plan(
    db: Session,
    pitch_id: uuid.UUID,
    *,
    source_take_id: uuid.UUID | None,
    script_version_id: uuid.UUID | None,
    target_ms: int | None,
    min_ms: int | None,
    max_ms: int | None,
    requested_at: datetime,
    payload: dict,
    meta: dict) -> bool:
    """계획 응답 하나를 한 행으로 더한다. 발표가 그 사이 지워졌으면 넣지 않고 False."""
    return PitchRepository(db).add_coaching_plan(
        CoachingPlan(
            pitch_id=pitch_id,
            source_take_id=source_take_id,
            script_version_id=script_version_id,
            target_ms=target_ms,
            min_ms=min_ms,
            max_ms=max_ms,
            requested_at=requested_at,
            payload=payload,
            meta=meta,
        )
    )


def pitch_timing(db: Session, pitch_id: uuid.UUID) -> tuple[int, int, int] | None:
    """제한 시간 · 허용 초과 · 허용 미달 (초). 코칭 계획의 바탕 값이라 바뀌면 계획을 다시 만든다."""
    pitch = PitchRepository(db).get_by_id(pitch_id)
    if pitch is None:
        return None
    return pitch.time_limit_sec, pitch.upper_deviation, pitch.lower_deviation
