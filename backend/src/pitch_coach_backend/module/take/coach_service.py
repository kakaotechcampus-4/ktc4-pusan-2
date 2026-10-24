"""코치 연동의 도메인 작업. 기능별 PR에서 단계적으로 연결한다."""

import logging
import uuid
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime, timedelta
from typing import Any

from psycopg.errors import ForeignKeyViolation
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from pitch_coach_backend.module.pitch import service as pitch_service
from pitch_coach_backend.module.take.coach_repository import (
    REVIEW,
    TAKE_RESULT,
    CoachRepository,
    is_waiting_for_result,
)
from pitch_coach_backend.module.take.dto import (
    FinalizeRows,
    MetricSecond,
    MissionInput,
    SlideEvent,
    SlideTiming,
    StoredEvent,
    TakeCoachValues,
    TranscriptSegment,
)
from pitch_coach_backend.module.take.entity import Mission, Take
from pitch_coach_backend.module.take.exception import NonExistentTake
from pitch_coach_backend.module.take.repository import json_ints
from pitch_coach_backend.module.user import service as user_service


def start_take(db: Session, take_id: uuid.UUID) -> bool:
    """첫 실시간 연결이 붙었다. `READY` 면 `RUNNING` 으로 바꾸고, 이 Take 에 쓸 값을 고정하고
    커밋한다. 바꿨으면 True. 이미 `RUNNING` 이거나(다시 붙은 연결) 끝났으면 아무것도 바꾸지 않는다.

    고정하는 값 (1-3 1번): 실은 미션 id 목록(`CoachRepository.assign_missions`) · 최신 평가 기준
    버전 · 원자료 사용 동의.

    발표 행을 먼저 잠근다. 새 Take 를 만드는 요청도 발표 행을 잠근 뒤 `READY` Take 를 끝내므로,
    순서가 거꾸로면 둘이 서로를 기다린다.
    """
    repo = CoachRepository(db)
    pitch_id = repo.pitch_id_of(take_id)
    if pitch_id is None:
        return False
    pitch_service.lock_for_new_version(db, pitch_id)
    take_number = repo.mark_started(take_id)
    if take_number is None:
        db.commit()
        return False
    mission_ids = repo.assign_missions(take_id, pitch_id, before=take_number)
    pitch = pitch_service.find_pitch(db, pitch_id)
    owner = user_service.find(db, pitch.user_id) if pitch is not None else None
    repo.fix_started_values(
        take_id,
        mission_ids=mission_ids,
        standard_version=pitch_service.latest_analysis_version(db, pitch_id),
        raw_data_consent=bool(owner is not None and owner.raw_data_consent),
    )
    db.commit()
    return True


def append_slide_events(db: Session, take_id: uuid.UUID, events: Iterable[SlideEvent]) -> None:
    """장 변경을 저장한다. 같은 시각은 먼저 저장한 것을 둔다. Take 가 지워졌으면 `NonExistentTake`.
    """
    rows = [{"slide_number": e.slide_number, "started_ms": e.started_ms} for e in events]
    try:
        CoachRepository(db).append_slide_events(take_id, rows)
    except IntegrityError as e:
        db.rollback()
        if isinstance(e.orig, ForeignKeyViolation):
            raise NonExistentTake() from e
        raise
    db.commit()


def take_coach_values(db: Session, take_id: uuid.UUID) -> TakeCoachValues | None:
    """발표 중 코치가 들고 있을 값의 재료 (architecture.md 1-3 1번). Take 나 발표가 없으면 None.

    미션 · 평가 기준 버전 · 동의는 Take 를 시작할 때 고정한 값을 읽는다. 반복 문제 · 코칭 계획의
    바탕 Take 는 이 Take 보다 앞선 Take 에서 고른다.

    시간 계획 재료는 지금 발표 값이다. 앱이 다시 떠 finalize 를 원자료로 다시 할 때 Take 시작 뒤
    제한 시간 · 허용 범위가 바뀌었으면 시간 판정 기준이 원래 Take 와 달라진다 (coach-agent.md 15절
    알려진 한계).
    """
    repo = CoachRepository(db)
    take = repo.take(take_id)
    if take is None:
        return None
    return _take_coach_values(db, repo, take)

def stored_metric_seconds(db: Session, take_id: uuid.UUID) -> tuple[MetricSecond, ...]:
    """프로세스를 다시 띄운 뒤에도 (take_id, t_ms)의 첫 저장본을 유지한다."""
    return tuple(
        MetricSecond(r.t_ms, r.slide_number, r.gaze, r.voice, r.stt_status)
        for r in CoachRepository(db).metric_seconds(take_id)
    )

def _take_coach_values(db: Session, repo: CoachRepository, take: Take) -> TakeCoachValues | None:
    pitch = pitch_service.find_pitch(db, take.pitch_id)
    if pitch is None:
        return None
    script = pitch_service.find_script_version(db, take.script_version_id)
    reviewed = repo.latest_reviewed_take(take.pitch_id, before=take.take_number)
    return TakeCoachValues(
        take_id=take.id,
        pitch_id=take.pitch_id,
        mode=take.mode,
        script_mode=take.script_mode,
        goal_time_sec=take.goal_time_sec,
        time_limit_sec=pitch.time_limit_sec,
        lower_deviation_sec=pitch.lower_deviation,
        upper_deviation_sec=pitch.upper_deviation,
        script_version_id=take.script_version_id,
        slides=_slide_timings(pitch_service.script_slides(db, take.script_version_id)),
        parsed_target_ms=_parsed_target_ms(script.parse_meta if script else None),
        missions=tuple(_mission_input(m) for m in repo.missions(_mission_ids(take.mission_ids))),
        recurring_issues=_recurring_issues(repo, reviewed),
        source_take_id=repo.latest_completed_take(take.pitch_id, before=take.take_number),
        script_used=pitch_service.script_used(db, take.pitch_id, take.standard_version),
        base_level_db=repo.base_level_db(take.id),
        slide_events=tuple(
            SlideEvent(e.slide_number, e.started_ms) for e in repo.slide_events(take.id)
        ),
    )

def _slide_timings(slides: Iterable[Any]) -> tuple[SlideTiming, ...]:
    """대본 장(`ScriptSlide`)의 시간 계획 재료."""
    return tuple(SlideTiming(s.slide_number, s.expected_ms, s.script_chars) for s in slides)

def _parsed_target_ms(parse_meta: Any) -> int | None:
    """대본을 파싱할 때 요청한 목표 시간 (script-parser.md 12-2). 없거나 모양이 틀리면 None."""
    if not isinstance(parse_meta, Mapping):
        return None
    value = parse_meta.get("target_ms")
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return None
    return value

def _mission_ids(values: Any) -> list[uuid.UUID]:
    ids: list[uuid.UUID] = []
    for value in values or []:
        try:
            ids.append(uuid.UUID(str(value)))
        except ValueError:
            logger.warning("takes.mission_ids 에 UUID 가 아닌 값이 있다 value=%r", value)
    return ids

def _mission_input(mission: Mission) -> MissionInput:
    return MissionInput(
        mission_id=str(mission.id),
        area=mission.area,
        slide_number=mission.slide_number,
        target=mission.target,
        priority=mission.priority,
        description=mission.description,
    )

def _recurring_issues(
    repo: CoachRepository, reviewed: uuid.UUID | None
) -> tuple[dict[str, Any], ...]:
    """그 Take 의 리뷰가 꼽은 반복 문제. BE 는 항목을 고치지 않고 그대로 싣는다 (1-5)."""
    if reviewed is None:
        return ()
    review = repo.ai_result(reviewed, REVIEW)
    payload = review.payload if review is not None else None
    issues = payload.get("recurring_issues") if isinstance(payload, dict) else None
    if not isinstance(issues, list):
        return ()
    return tuple(issue for issue in issues if isinstance(issue, dict))

def append_coach_events(db: Session, take_id: uuid.UUID, events: Sequence[StoredEvent]) -> None:
    """evaluate 응답 하나의 이벤트를 한 트랜잭션으로 저장한다. Take 가 지워졌으면 `NonExistentTake`.
    """
    try:
        CoachRepository(db).append_events(take_id, events)
    except NonExistentTake:
        db.rollback()
        raise
    db.commit()

def load_coach_events(db: Session, take_id: uuid.UUID) -> list[dict[str, Any]] | None:
    """저장된 코치 이벤트를 받은 순서대로 (finalize 의 `events`). Take 가 Take 결과를 기다리는 중이
    아니면(지움 · 이미 끝남) None — AI 를 부르지 않게."""
    repo = CoachRepository(db)
    take = repo.take(take_id)
    if take is None or not is_waiting_for_result(take):
        return None
    return [dict(e.payload) for e in repo.coach_events(take_id)]


def chosen_plan_payload(db, pitch_id, **basis):
    """코칭 계획 저장 기능이 연결되기 전에는 기본 코칭을 사용한다."""
    return None


logger = logging.getLogger(__name__)


def finalize_rows(db: Session, take_id: uuid.UUID) -> FinalizeRows | None:
    """finalize 를 DB 에서 다시 만들 재료. Take 가 Take 결과를 기다리는 중이 아니면 None."""
    repo = CoachRepository(db)
    take = repo.take(take_id)
    if take is None or not is_waiting_for_result(take):
        return None
    values = _take_coach_values(db, repo, take)
    if values is None:
        return None
    return FinalizeRows(
        values=values,
        seconds=tuple(
            MetricSecond(r.t_ms, r.slide_number, r.gaze, r.voice, r.stt_status)
            for r in repo.metric_seconds(take_id)
        ),
        segments=tuple(
            TranscriptSegment(end_ms=s.end_ms, words=s.words, final_at_ms=s.final_at_ms)
            for s in repo.transcript_segments(take_id)
        ),
        utterance_ends=tuple(json_ints(take.utterance_ends)),
        events=tuple(dict(e.payload) for e in repo.coach_events(take_id)),
        raw_data_consent=take.raw_data_consent,
    )

def save_take_result(
    db: Session,
    take_id: uuid.UUID,
    *,
    take_result: Mapping[str, Any],
    meta: Mapping[str, Any],
    events: Sequence[StoredEvent],
    measures: Mapping[str, Any],
    duration_sec: int,
) -> bool:
    """finalize 응답을 한 트랜잭션에 저장한다 (12-2). 저장했으면 True.

    이벤트(발표 중 못 넣은 것 + 이번 응답의 것, 받은 순서대로) · Take 결과 · take_summaries 측정
    칸 · Take 길이 · 분석 단계(`SCORING`) · 동의 없는 Take 의 1초 원자료 삭제를 같이 한다. Take
    행을 잠근 뒤 Take 가 아직 Take 결과를 기다리는 중(`ANALYZING` · `FINALIZING`)일 때만 커밋하고,
    아니면 응답을 버린다 — 재시작한 BE 와 앞 작업의 응답이 겹쳐도 이미 앞으로 간 단계를 되돌리지
    않게.
    """
    repo = CoachRepository(db)
    take = repo.lock_finalizing(take_id)
    if take is None:
        db.rollback()
        return False
    repo.append_events(take_id, events)
    repo.save_ai_result(take_id, TAKE_RESULT, take_result, meta)
    repo.save_summary_measures(take_id, measures)
    take.duration_sec = duration_sec
    take.analysis_stage = "SCORING"
    take.analysis_stage_at = func.now()
    if not take.raw_data_consent:
        # Take 결과가 생기면 replay 에 쓸 일이 없다
        repo.delete_metric_seconds(take_id)
    db.commit()
    return True

def fail_take_result(db: Session, take_id: uuid.UUID) -> bool:
    """Take 결과를 끝내 만들지 못했다. Take 를 `FAILED` 로 두고 원자료는 남긴다 (7일, 1-1)."""
    failed = CoachRepository(db).fail_finalizing(take_id)
    db.commit()
    return failed

def finalizing_takes(db: Session, stale_before: datetime | None = None) -> list[uuid.UUID]:
    """Take 결과를 기다리는 중으로 남은 Take. 앱이 다시 뜰 때 이어 간다 (1-3 'BE가 재시작할 때').
    `stale_before` 를 주면 그 전에 기다리기 시작한 것만 — 오래 남은 Take 를 다시 띄울 때 쓴다."""
    return CoachRepository(db).finalizing_take_ids(stale_before)


FAILED_RAW_RETENTION = timedelta(days=7)

def purge_expired_raw(db: Session, now: datetime) -> int:
    """동의 없이 FAILED 로 끝난 지 7일이 지난 Take 의 1초 원자료를 지운다. 지운 행 수."""
    deleted = CoachRepository(db).purge_expired_metric_seconds(now - FAILED_RAW_RETENTION)
    db.commit()
    return deleted
