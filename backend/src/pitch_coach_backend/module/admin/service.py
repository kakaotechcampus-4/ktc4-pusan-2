"""관리자 콘솔 집계. 읽기만 하고 커밋하지 않는다."""

import logging
import uuid
from datetime import UTC, datetime, time, timedelta
from typing import Any

from sqlalchemy.orm import Session

from pitch_coach_backend.core.limits import KST
from pitch_coach_backend.module import hooks
from pitch_coach_backend.module.admin import repository
from pitch_coach_backend.module.admin.dto import (
    AdminAIResult,
    AdminCalibration,
    AdminChatDay,
    AdminChatDiagnostic,
    AdminCoachEvent,
    AdminCoachingPlan,
    AdminExportResult,
    AdminLiveSecond,
    AdminLiveSegment,
    AdminLiveWord,
    AdminPage,
    AdminPitchAI,
    AdminScriptDetail,
    AdminScriptSlide,
    AdminSegmentWords,
    AdminSlideEvent,
    AdminStandardAnalysis,
    AdminSuggestionRow,
    AdminTakeExport,
    AdminTakeInputs,
    AdminTakeLive,
    AdminTakeResults,
    AdminTakeRow,
    AdminTakeSummary,
    AdminTakeSurveyRow,
    AdminTakeSurveySummary,
    AdminUserRow,
    DailyCount,
    PracticeTimeStats,
    StatsResponse,
    TakeStats,
    UserStats,
)
from pitch_coach_backend.module.admin.exception import AdminChatQueryInvalid
from pitch_coach_backend.module.pitch.exception import NonExistentPitch
from pitch_coach_backend.module.take.exception import NonExistentTake
from pitch_coach_backend.module.take.measures import word_text
from pitch_coach_backend.module.take.repository import (
    ANALYSIS_STALL_AFTER,
    CONFIRMATION_WAIT,
    TakeRepository,
)
from pitch_coach_backend.module.take.service.lifecycle import ABANDON_AFTER_SEC
from pitch_coach_backend.module.take.states import REVIEW, TAKE_RESULT

DAILY_DAYS = 30
USER_LIMIT = 500
# 연결이 끊긴 Take 는 ABANDON_AFTER_SEC 뒤에 이탈로 끝난다 (창을 닫았으면 더 일찍). 연결된 채
# 말이 없으면 말 없음 자동 종료로 끝나서, 확정 전사가 그보다 오래 멈춰 있지 않다. 마지막 활동 뒤
# 이탈 대기보다 여유만큼 더 지났으면 멈춘 것으로 본다
STUCK_MARGIN = timedelta(minutes=10)
STUCK_LIMIT = 50
# 사용자가 직접 끝낸 Take 는 end_reason 이 비어 있다.
# 비율에서 자동 종료와 나란히 보이게 이름을 붙인다
USER_ENDED = "USER"
LIVE_LIMIT = 500
COACH_EVENT_LIMIT = 2000
SEGMENT_LIMIT = 5000
PLAN_LIMIT = 20

# 코치 대화 운영 기록을 보는 기본 기간과 상한. 운영 기록은 180일 둔다(chat-agent.md 12-2)
CHAT_DAYS = 14
CHAT_DAYS_MAX = 180

logger = logging.getLogger(__name__)


def get_stats(db: Session) -> StatsResponse:
    today = datetime.now(UTC).astimezone(KST).date()
    today_start = datetime.combine(today, time(), KST)
    first_day = today - timedelta(days=DAILY_DAYS - 1)
    first_day_start = datetime.combine(first_day, time(), KST)

    signups = repository.daily_signups(db, first_day_start)
    takes = repository.daily_takes(db, first_day_start)
    users_total, users_today = repository.count_users(db, today_start)
    takes_total, takes_today = repository.count_takes(db, today_start)
    breakdowns = repository.take_breakdowns(db)
    end_reasons = breakdowns.pop("by_end_reason")

    return StatsResponse(
        users=UserStats(total=users_total, today=users_today),
        takes=TakeStats(
            total=takes_total,
            today=takes_today,
            **breakdowns,
            by_end_reason={reason or USER_ENDED: n for reason, n in end_reasons.items()},
        ),
        practice_time=PracticeTimeStats(**repository.practice_time(db)),
        daily=[
            DailyCount(date=day, signups=signups.get(day, 0), takes=takes.get(day, 0))
            for day in (first_day + timedelta(days=i) for i in range(DAILY_DAYS))
        ],
    )


def list_users(db: Session) -> list[AdminUserRow]:
    return [AdminUserRow(**row) for row in repository.list_users(db, USER_LIMIT)]


def list_takes(db: Session, *, status: str | None, limit: int) -> list[AdminTakeRow]:
    return [AdminTakeRow(**row) for row in repository.list_takes(db, status=status, limit=limit)]


def list_stuck_takes(db: Session, now: datetime | None = None) -> list[AdminTakeRow]:
    now = now or datetime.now(UTC)
    active_before = now - timedelta(seconds=ABANDON_AFTER_SEC) - STUCK_MARGIN
    rows = repository.list_stuck_takes(db, active_before, STUCK_LIMIT)
    return [AdminTakeRow(**row) for row in rows]


def list_stalled_analyses(db: Session, now: datetime | None = None) -> list[AdminTakeRow]:
    now = now or datetime.now(UTC)
    stage_before = now - ANALYSIS_STALL_AFTER
    rows = repository.list_stalled_analyses(
        db,
        stage_before=stage_before,
        confirmation_before=stage_before - CONFIRMATION_WAIT,
        limit=STUCK_LIMIT,
    )
    return [AdminTakeRow(**row) for row in rows]


def get_take_live(
    db: Session, take_id: uuid.UUID, *, after_seq: int, after_t_ms: int | None
) -> AdminTakeLive:
    status = repository.take_status(db, take_id)
    if status is None:
        raise NonExistentTake()
    segments = repository.live_segments(db, take_id, after_seq=after_seq, limit=LIVE_LIMIT)
    seconds = repository.live_seconds(db, take_id, after_t_ms=after_t_ms, limit=LIVE_LIMIT)
    return AdminTakeLive(
        status=status,
        segments=[_live_segment(row) for row in segments],
        seconds=[AdminLiveSecond(**row) for row in seconds],
        slide_events=_slide_events(db, take_id),
    )


def get_take_script(db: Session, take_id: uuid.UUID) -> list[AdminScriptSlide]:
    if repository.take_status(db, take_id) is None:
        raise NonExistentTake()
    return [AdminScriptSlide(**row) for row in repository.take_script_slides(db, take_id)]


def get_take_results(db: Session, take_id: uuid.UUID) -> AdminTakeResults:
    if repository.take_status(db, take_id) is None:
        raise NonExistentTake()
    summary = repository.take_summary(db, take_id)
    return AdminTakeResults(
        ai_results=[_ai_result(row) for row in repository.ai_results(db, take_id)],
        coach_events=[
            AdminCoachEvent(**row)
            for row in repository.coach_events(db, take_id, COACH_EVENT_LIMIT)
        ],
        summary=None if summary is None else AdminTakeSummary.model_validate(summary),
    )


def get_take_inputs(db: Session, take_id: uuid.UUID) -> AdminTakeInputs:
    settings = repository.take_settings(db, take_id)
    if settings is None:
        raise NonExistentTake()
    calibration = repository.calibration(db, take_id)
    return AdminTakeInputs(
        **settings,
        slide_events=_slide_events(db, take_id),
        segments=[
            AdminSegmentWords(**row)
            for row in repository.transcript_words(db, take_id, SEGMENT_LIMIT)
        ],
        calibration=None if calibration is None else AdminCalibration.model_validate(calibration),
    )


def export_take(db: Session, take_id: uuid.UUID) -> AdminTakeExport:
    settings = repository.take_settings(db, take_id)
    if settings is None:
        raise NonExistentTake()
    return AdminTakeExport(
        take_id=take_id,
        raw_data_consent=settings["raw_data_consent"],
        metric_second_count=repository.count_metric_seconds(db, take_id),
        request=hooks.finalize_export().recovery_request(db, take_id),
        results={
            row["kind"]: AdminExportResult(
                payload=row["payload"], meta=row["meta"], revision=row["revision"]
            )
            for row in repository.ai_results(db, take_id)
        },
    )


def _live_segment(row: dict[str, Any]) -> AdminLiveSegment:
    # 리포트 채점이 장을 나눌 때와 같은 글자 · 시각이다 (`TakeRepository.get_transcript_words`)
    words = [AdminLiveWord(word=word_text(w), start_ms=w["start_ms"]) for w in row["words"]]
    return AdminLiveSegment(**{**row, "words": words})


def _slide_events(db: Session, take_id: uuid.UUID) -> list[AdminSlideEvent]:
    return [
        AdminSlideEvent(slide_number=number, started_ms=started_ms)
        for number, started_ms in TakeRepository(db).get_slide_events(take_id)
    ]


def _ai_result(row: dict[str, Any]) -> AdminAIResult:
    payload = row["payload"] if isinstance(row["payload"], dict) else {}
    meta = row["meta"] if isinstance(row["meta"], dict) else {}
    take_result = payload if row["kind"] == TAKE_RESULT else {}
    review = payload if row["kind"] == REVIEW else {}
    return AdminAIResult(
        **row,
        replayed=take_result.get("replayed"),
        criteria_changed=take_result.get("criteria_changed"),
        measured_ratios={
            area: result.get("measured_ratio")
            for area, result in (take_result.get("areas") or {}).items()
            if isinstance(result, dict)
        },
        fallback_reason=review.get("fallback_reason"),
        model=meta.get("model"),
    )


def get_pitch_ai(db: Session, pitch_id: uuid.UUID) -> AdminPitchAI:
    if not repository.pitch_exists(db, pitch_id):
        raise NonExistentPitch()
    scripts = [
        AdminScriptDetail(**{**row, "missing_rubric_slides": row["missing_rubric_slides"] or []})
        for row in repository.script_versions(db, pitch_id)
    ]
    counts = repository.standard_criteria_counts(db, pitch_id)
    analyses = {row["version"]: row for row in repository.standard_analyses(db, pitch_id)}
    standards = [
        AdminStandardAnalysis(
            **analyses.get(version, {"version": version}),
            criteria_count=counts.get(version, 0),
        )
        for version in sorted(counts.keys() | analyses.keys(), reverse=True)
    ]
    plans = repository.coaching_plans(db, pitch_id, PLAN_LIMIT)
    return AdminPitchAI(
        scripts=scripts,
        standards=standards,
        coaching_plans=[AdminCoachingPlan(**row) for row in plans],
    )


def _page[T](rows: list[dict[str, Any]], model: type[T], limit: int, offset: int) -> AdminPage[T]:
    """저장소가 `_fetch_page` 로 한 행 더 읽어 온 결과. 넘치면 다음 쪽이 있다."""
    return AdminPage[model](
        items=[model(**row) for row in rows[:limit]],
        next_offset=offset + limit if len(rows) > limit else None,
    )


def take_survey_summary(db: Session) -> list[AdminTakeSurveySummary]:
    return [
        AdminTakeSurveySummary(
            question_version=row["question_version"],
            survey_count=row["survey_count"],
            avg_accuracy=float(row["avg_accuracy"]),
            avg_usefulness=float(row["avg_usefulness"]),
        )
        for row in repository.take_survey_summary(db)
    ]


def list_take_surveys(db: Session, *, limit: int, offset: int) -> AdminPage[AdminTakeSurveyRow]:
    rows = repository.list_take_surveys(db, limit=limit, offset=offset)
    return _page(rows, AdminTakeSurveyRow, limit, offset)


def list_suggestions(
    db: Session,
    *,
    limit: int,
    offset: int,
    category: str | None = None,
    user_id: uuid.UUID | None = None,
) -> AdminPage[AdminSuggestionRow]:
    rows = repository.list_suggestions(
        db, category=category, limit=limit, offset=offset, user_id=user_id
    )
    return _page(rows, AdminSuggestionRow, limit, offset)


def chat_days(db: Session, days: int) -> list[AdminChatDay]:
    since = datetime.now(UTC).astimezone(KST).date() - timedelta(days=days - 1)
    rows = repository.chat_days(db, since)
    # 대화 관리자 API 는 조회한 사실을 남긴다(chat-agent.md 12-3). 조건만 적는다
    logger.info("관리자가 대화 운영 기록을 조회했다 days=%d", days)
    return [AdminChatDay(**row) for row in rows]


def chat_diagnostics(
    db: Session,
    *,
    run_id: uuid.UUID | None,
    conversation_id: uuid.UUID | None,
    limit: int,
    offset: int,
) -> AdminPage[AdminChatDiagnostic]:
    if (run_id is None) == (conversation_id is None):
        raise AdminChatQueryInvalid()
    rows = repository.chat_diagnostics(
        db, run_id=run_id, conversation_id=conversation_id, limit=limit, offset=offset
    )
    items = [AdminChatDiagnostic.model_validate(row, from_attributes=True) for row in rows[:limit]]
    logger.info(
        "관리자가 대화 진단 기록을 조회했다 run=%s conversation=%s rows=%d",
        run_id,
        conversation_id,
        len(items),
    )
    return AdminPage[AdminChatDiagnostic](
        items=items,
        next_offset=offset + limit if len(rows) > limit else None,
    )
