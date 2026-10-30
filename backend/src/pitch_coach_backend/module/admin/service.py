"""관리자 콘솔 집계. 읽기만 하고 커밋하지 않는다."""

import uuid
from datetime import UTC, datetime, time, timedelta
from typing import Any

from sqlalchemy.orm import Session

from pitch_coach_backend.module import hooks
from pitch_coach_backend.module.admin import repository
from pitch_coach_backend.module.admin.dto import (
    AdminAIResult,
    AdminCalibration,
    AdminCoachEvent,
    AdminCoachingPlan,
    AdminExportResult,
    AdminLiveSecond,
    AdminLiveSegment,
    AdminPage,
    AdminPitchAI,
    AdminScriptDetail,
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
from pitch_coach_backend.module.admin.repository import KST
from pitch_coach_backend.module.pitch.exception import NonExistentPitch
from pitch_coach_backend.module.take.entity import Take
from pitch_coach_backend.module.take.exception import NonExistentTake
from pitch_coach_backend.module.take.repository import CONFIRMATION_WAIT
from pitch_coach_backend.module.take.service.analysis_runner import (
    STALE_ANALYSIS,
    SWEEP_INTERVAL_SEC,
)
from pitch_coach_backend.module.take.service.lifecycle import ABANDON_AFTER_SEC

DAILY_DAYS = 30
USER_LIMIT = 500
# 연결이 끊긴 Take 는 ABANDON_AFTER_SEC 뒤에 이탈로 끝난다 (창을 닫았으면 더 일찍). 연결된 채
# 말이 없으면 말 없음 자동 종료로 끝나서, 확정 전사가 그보다 오래 멈춰 있지 않다. 마지막 활동 뒤
# 이탈 대기보다 여유만큼 더 지났으면 멈춘 것으로 본다
STUCK_MARGIN = timedelta(minutes=10)
STUCK_LIMIT = 50
ANALYSIS_STALL_AFTER = STALE_ANALYSIS + timedelta(seconds=SWEEP_INTERVAL_SEC)
# 사용자가 직접 끝낸 Take 는 end_reason 이 비어 있다.
# 비율에서 자동 종료와 나란히 보이게 이름을 붙인다
USER_ENDED = "USER"
# 사용자별 평점 · 의견에서 한 행에 미리 싣는 의견 수. 마이페이지 의견은 수에 제한이 없다
GROUP_SUGGESTION_PREVIEW = 3
LIVE_LIMIT = 500
COACH_EVENT_LIMIT = 2000
SEGMENT_LIMIT = 5000
PLAN_LIMIT = 20


def get_stats(db: Session, now: datetime | None = None) -> StatsResponse:
    now = now or datetime.now(UTC)
    today = now.astimezone(KST).date()
    today_start = datetime.combine(today, time(), KST)
    first_day = today - timedelta(days=DAILY_DAYS - 1)
    first_day_start = datetime.combine(first_day, time(), KST)

    signups = repository.daily_signups(db, first_day_start)
    takes = repository.daily_takes(db, first_day_start)
    users_total, users_today = repository.count_users(db, today_start)
    takes_total, takes_today = repository.count_takes(db, today_start)
    count, total, avg, median, ratio = repository.practice_time(db)
    end_reasons = repository.count_takes_by(db, Take.end_reason, ended_only=True)

    return StatsResponse(
        users=UserStats(total=users_total, today=users_today),
        takes=TakeStats(
            total=takes_total,
            today=takes_today,
            by_status=_named(repository.count_takes_by(db, Take.status)),
            by_mode=_named(repository.count_takes_by(db, Take.mode)),
            by_script_mode=_named(repository.count_takes_by(db, Take.script_mode)),
            by_end_reason={reason or USER_ENDED: n for reason, n in end_reasons.items()},
        ),
        practice_time=PracticeTimeStats(
            measured_takes=count,
            total_sec=total,
            avg_sec=avg,
            median_sec=median,
            avg_goal_ratio=ratio,
        ),
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
        segments=[AdminLiveSegment(**row) for row in segments],
        seconds=[AdminLiveSecond(**row) for row in seconds],
    )


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
        slide_events=[AdminSlideEvent(**row) for row in repository.slide_events(db, take_id)],
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


def _ai_result(row: dict[str, Any]) -> AdminAIResult:
    payload = row["payload"] if isinstance(row["payload"], dict) else {}
    meta = row["meta"] if isinstance(row["meta"], dict) else {}
    areas = payload.get("areas") if row["kind"] == "TAKE_RESULT" else None
    return AdminAIResult(
        **row,
        replayed=payload.get("replayed") if row["kind"] == "TAKE_RESULT" else None,
        criteria_changed=payload.get("criteria_changed") if row["kind"] == "TAKE_RESULT" else None,
        measured_ratios={
            area: result.get("measured_ratio")
            for area, result in (areas or {}).items()
            if isinstance(result, dict)
        },
        fallback_reason=payload.get("fallback_reason") if row["kind"] == "REVIEW" else None,
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


def _named(counts: dict[str | None, int]) -> dict[str, int]:
    # NOT NULL 컬럼이라 None 키는 없다
    return {key: n for key, n in counts.items() if key is not None}


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
    return AdminPage[AdminTakeSurveyRow](
        items=[AdminTakeSurveyRow(**row) for row in rows[:limit]],
        next_offset=offset + limit if len(rows) > limit else None,
    )


def list_suggestions(
    db: Session,
    *,
    source: str | None,
    limit: int,
    offset: int,
    category: str | None = None,
    user_id: uuid.UUID | None = None,
) -> AdminPage[AdminSuggestionRow]:
    rows = repository.list_suggestions(
        db, source=source, category=category, limit=limit, offset=offset, user_id=user_id
    )
    return AdminPage[AdminSuggestionRow](
        items=[AdminSuggestionRow(**row) for row in rows[:limit]],
        next_offset=offset + limit if len(rows) > limit else None,
    )
