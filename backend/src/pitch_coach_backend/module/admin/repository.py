"""관리자 콘솔 집계 쿼리. 읽기만 한다."""

import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy import (
    ColumnElement,
    Integer,
    Select,
    Uuid,
    and_,
    case,
    cast,
    distinct,
    extract,
    func,
    null,
    or_,
    select,
    union_all,
)
from sqlalchemy.dialects.postgresql import aggregate_order_by
from sqlalchemy.orm import InstrumentedAttribute, Session

from pitch_coach_backend.core.config import settings
from pitch_coach_backend.module.pitch.entity import (
    CoachingPlan,
    Pitch,
    ScriptSlide,
    ScriptVersion,
    StandardAnalyses,
    Standards,
)
from pitch_coach_backend.module.service_feedback.entity import ServiceFeedback, UserSuggestion
from pitch_coach_backend.module.take.entity import (
    Calibration,
    CoachEvent,
    Take,
    TakeAIResults,
    TakeMetricSecond,
    TakeSlideEvent,
    TakeSummary,
    TakeTranscriptSegment,
)
from pitch_coach_backend.module.take.repository import last_active_at
from pitch_coach_backend.module.user.entity import User

# 일별 집계의 날짜 기준. 사용자가 모두 한국에 있다.
# SQL 은 DB 가 아는 시간대 이름을, 파이썬은 고정 오프셋을 쓴다.
# 컨테이너에 시간대 자료가 없어도 되게. 한국은 일광 절약 시간이 없어 둘이 같다
DAY_TIMEZONE = "Asia/Seoul"
KST = timezone(timedelta(hours=9), "KST")

# 실제로 연습한 시간(초). duration_sec 은 분석 결과가 와야 채워진다.
# 그 전에는 시작 · 끝 시각으로 잰다.
_RAW_ELAPSED_SEC = func.coalesce(
    Take.duration_sec, extract("epoch", Take.ended_at - Take.started_at)
)
# 시작 시각은 FE 시계, 자동 종료 · 이탈의 끝 시각은 서버 시계라 어긋나면 음수가 된다.
# 음수는 못 잰 것으로 본다. Take 최대 길이보다 길면 그 길이로 자른다.
# 시각 범위 검사(#621) 전에 저장된 0001년 같은 시작 시각이 합계 · 평균을 망가뜨리지 않게.
# 못 쟀거나 둘 다 없으면(시작 전 · 진행 중) NULL 이라 모든 합계 · 평균에서 똑같이 빠진다
ELAPSED_SEC: ColumnElement[Any] = case(
    (
        _RAW_ELAPSED_SEC >= 0,
        func.least(_RAW_ELAPSED_SEC, settings.take_max_duration_sec),
    )
)
# 입력 검증(#147) 전에 만든 Take 는 목표가 0 이하일 수 있다.
# 0 으로 나누면 쿼리가 실패하니 비율에서 뺀다
_GOAL_SEC = case((Take.goal_time_sec > 0, Take.goal_time_sec))


def count_users(db: Session, since: datetime) -> tuple[int, int]:
    """(전체, `since` 뒤에 가입한 수)."""
    return _count_total_and_since(db, User.created_at, since)


def count_takes(db: Session, since: datetime) -> tuple[int, int]:
    """(전체, `since` 뒤에 만든 수)."""
    return _count_total_and_since(db, Take.created_at, since)


def count_takes_by(
    db: Session, column: InstrumentedAttribute[Any], *, ended_only: bool = False
) -> dict[str | None, int]:
    stmt = select(column, func.count()).group_by(column)
    if ended_only:
        stmt = stmt.where(Take.ended_at.is_not(None))
    return {value: count for value, count in db.execute(stmt).all()}


def practice_time(db: Session) -> tuple[int, float, float | None, float | None, float | None]:
    """(잰 Take 수, 합계, 평균, 중앙값, 목표 시간 대비 평균 비율)."""
    row = db.execute(
        select(
            func.count(ELAPSED_SEC),
            func.coalesce(func.sum(ELAPSED_SEC), 0),
            func.avg(ELAPSED_SEC),
            func.percentile_cont(0.5).within_group(ELAPSED_SEC),
            func.avg(ELAPSED_SEC / _GOAL_SEC),
        )
    ).one()
    count, total, avg, median, ratio = row
    return (
        int(count),
        float(total),
        _float_or_none(avg),
        _float_or_none(median),
        _float_or_none(ratio),
    )


def daily_signups(db: Session, since: datetime) -> dict[date, int]:
    return _count_per_day(db, User.created_at, since)


def daily_takes(db: Session, since: datetime) -> dict[date, int]:
    return _count_per_day(db, Take.created_at, since)


def list_users(db: Session, limit: int) -> list[dict[str, Any]]:
    """사용자별 발표 · 연습 수와 총 연습 시간. 최근에 연습한 사용자부터."""
    last_take_at = func.max(Take.created_at)
    rows = db.execute(
        select(
            User.id,
            User.name,
            User.email,
            User.created_at,
            func.count(distinct(Pitch.id)).label("pitch_count"),
            func.count(Take.id).label("take_count"),
            func.coalesce(func.sum(ELAPSED_SEC), 0).label("total_practice_sec"),
            last_take_at.label("last_take_at"),
        )
        .outerjoin(Pitch, Pitch.user_id == User.id)
        .outerjoin(Take, Take.pitch_id == Pitch.id)
        .group_by(User.id)
        .order_by(last_take_at.desc().nulls_last(), User.created_at.desc())
        .limit(limit)
    ).mappings()
    return [dict(row) for row in rows]


def list_takes(db: Session, *, status: str | None, limit: int) -> list[dict[str, Any]]:
    """최근 Take 부터. status 를 주면 그 상태만."""
    stmt = _take_rows()
    if status is not None:
        stmt = stmt.where(Take.status == status)
    return _fetch(db, stmt.order_by(Take.created_at.desc()).limit(limit))


def list_stuck_takes(db: Session, active_before: datetime, limit: int) -> list[dict[str, Any]]:
    """진행 중(`RUNNING`)인데 `active_before` 뒤로 활동이 없는 Take. 오래된 것부터.

    활동 시각은 앱이 다시 뜰 때 떠난 Take 를 찾는 기준과 같다
    (`TakeRepository.running_takes_last_active`). 시작 시각만으로는 못 잰다.
    FE 가 발표를 끝낼 때에야 보내서 진행 중에는 비어 있다.

    시작 전(`READY`)은 넣지 않는다. 만든 지 10분이 지나면 `ready_sweep` 이 이탈로 끝낸다.
    분석 중(`ANALYZING`)은 `list_stalled_analyses` 가 분석 단계 시각으로 따로 본다.
    """
    active_at = last_active_at()
    stmt = _take_rows().where(Take.status == "RUNNING", active_at < active_before)
    return _fetch(db, stmt.order_by(active_at).limit(limit))


def list_stalled_analyses(
    db: Session, *, stage_before: datetime, confirmation_before: datetime, limit: int
) -> list[dict[str, Any]]:
    waited_too_long = case(
        (
            Take.analysis_stage == "AWAITING_CONFIRMATION",
            Take.analysis_stage_at < confirmation_before,
        ),
        else_=Take.analysis_stage_at < stage_before,
    )
    stmt = _take_rows().where(
        Take.status == "ANALYZING",
        or_(
            Take.analysis_hold_reason.is_not(None),
            Take.analysis_stage_at.is_(None),
            waited_too_long,
        ),
    )
    return _fetch(db, stmt.order_by(Take.analysis_stage_at.asc().nulls_first()).limit(limit))


def take_status(db: Session, take_id: uuid.UUID) -> str | None:
    return db.scalar(select(Take.status).where(Take.id == take_id))


def live_segments(
    db: Session, take_id: uuid.UUID, *, after_seq: int, limit: int
) -> list[dict[str, Any]]:
    stmt = (
        select(
            TakeTranscriptSegment.seq,
            TakeTranscriptSegment.stt_session_no,
            TakeTranscriptSegment.start_ms,
            TakeTranscriptSegment.end_ms,
            TakeTranscriptSegment.transcript,
            TakeTranscriptSegment.confidence,
            TakeTranscriptSegment.speech_final,
        )
        .where(TakeTranscriptSegment.take_id == take_id, TakeTranscriptSegment.seq > after_seq)
        .order_by(TakeTranscriptSegment.seq)
        .limit(limit)
    )
    return _fetch(db, stmt)


def live_seconds(
    db: Session, take_id: uuid.UUID, *, after_t_ms: int | None, limit: int
) -> list[dict[str, Any]]:
    stmt = select(
        TakeMetricSecond.t_ms,
        TakeMetricSecond.slide_number,
        TakeMetricSecond.gaze,
        TakeMetricSecond.voice,
        TakeMetricSecond.stt_status,
    ).where(TakeMetricSecond.take_id == take_id)
    if after_t_ms is not None:
        stmt = stmt.where(TakeMetricSecond.t_ms > after_t_ms)
    return _fetch(db, stmt.order_by(TakeMetricSecond.t_ms).limit(limit))


AI_RESULT_KINDS = ("TAKE_RESULT", "SCRIPT_COVERAGE", "SCRIPT_COVERAGE_PENDING", "REVIEW")


def ai_results(db: Session, take_id: uuid.UUID) -> list[dict[str, Any]]:
    order = case(
        {kind: i for i, kind in enumerate(AI_RESULT_KINDS)},
        value=TakeAIResults.kind,
        else_=len(AI_RESULT_KINDS),
    )
    stmt = (
        select(
            TakeAIResults.kind,
            TakeAIResults.revision,
            TakeAIResults.created_at,
            TakeAIResults.payload,
            TakeAIResults.meta,
        )
        .where(TakeAIResults.take_id == take_id)
        .order_by(order, TakeAIResults.kind)
    )
    return _fetch(db, stmt)


def coach_events(db: Session, take_id: uuid.UUID, limit: int) -> list[dict[str, Any]]:
    stmt = (
        select(
            CoachEvent.seq,
            CoachEvent.t_ms,
            CoachEvent.kind,
            CoachEvent.event_id,
            CoachEvent.payload,
            CoachEvent.meta,
        )
        .where(CoachEvent.take_id == take_id)
        .order_by(CoachEvent.seq)
        .limit(limit)
    )
    return _fetch(db, stmt)


def take_summary(db: Session, take_id: uuid.UUID) -> TakeSummary | None:
    return db.get(TakeSummary, take_id)


def take_settings(db: Session, take_id: uuid.UUID) -> dict[str, Any] | None:
    row = db.execute(
        select(
            Take.script_mode,
            Take.standard_version,
            Take.mission_ids,
            Take.raw_data_consent,
            Take.utterance_ends,
        ).where(Take.id == take_id)
    ).mappings().first()
    return None if row is None else dict(row)


def transcript_words(db: Session, take_id: uuid.UUID, limit: int) -> list[dict[str, Any]]:
    stmt = (
        select(
            TakeTranscriptSegment.seq,
            TakeTranscriptSegment.start_ms,
            TakeTranscriptSegment.end_ms,
            TakeTranscriptSegment.final_at_ms,
            TakeTranscriptSegment.words,
        )
        .where(TakeTranscriptSegment.take_id == take_id)
        .order_by(TakeTranscriptSegment.seq)
        .limit(limit)
    )
    return _fetch(db, stmt)


def slide_events(db: Session, take_id: uuid.UUID) -> list[dict[str, Any]]:
    stmt = (
        select(TakeSlideEvent.started_ms, TakeSlideEvent.slide_number)
        .where(TakeSlideEvent.take_id == take_id)
        .order_by(TakeSlideEvent.started_ms)
    )
    return _fetch(db, stmt)


def calibration(db: Session, take_id: uuid.UUID) -> Calibration | None:
    return db.scalar(select(Calibration).where(Calibration.take_id == take_id))


def count_metric_seconds(db: Session, take_id: uuid.UUID) -> int:
    stmt = select(func.count()).where(TakeMetricSecond.take_id == take_id)
    return db.scalar(stmt) or 0


def pitch_exists(db: Session, pitch_id: uuid.UUID) -> bool:
    return db.scalar(select(Pitch.id).where(Pitch.id == pitch_id)) is not None


def script_versions(db: Session, pitch_id: uuid.UUID) -> list[dict[str, Any]]:
    slides = (
        select(
            ScriptSlide.script_version_id,
            func.count().label("slide_count"),
            func.count(ScriptSlide.rubric).label("rubric_slide_count"),
            func.array_agg(aggregate_order_by(ScriptSlide.slide_number, ScriptSlide.slide_number))
            .filter(ScriptSlide.rubric.is_(None))
            .label("missing_rubric_slides"),
        )
        .group_by(ScriptSlide.script_version_id)
        .subquery()
    )
    stmt = (
        select(
            ScriptVersion.version,
            ScriptVersion.created_at,
            ScriptVersion.parse_status,
            ScriptVersion.parse_error,
            ScriptVersion.parse_requested_at,
            ScriptVersion.rubric_status,
            ScriptVersion.rubric_requested_at,
            ScriptVersion.rubric_retry_used,
            func.coalesce(slides.c.slide_count, 0).label("slide_count"),
            func.coalesce(slides.c.rubric_slide_count, 0).label("rubric_slide_count"),
            slides.c.missing_rubric_slides,
        )
        .outerjoin(slides, slides.c.script_version_id == ScriptVersion.id)
        .where(ScriptVersion.pitch_id == pitch_id)
        .order_by(ScriptVersion.version.desc())
    )
    return _fetch(db, stmt)


def standard_criteria_counts(db: Session, pitch_id: uuid.UUID) -> dict[int, int]:
    rows = db.execute(
        select(Standards.version, func.count())
        .where(Standards.pitch_id == pitch_id)
        .group_by(Standards.version)
    ).all()
    return {version: count for version, count in rows}


def standard_analyses(db: Session, pitch_id: uuid.UUID) -> list[dict[str, Any]]:
    stmt = select(
        StandardAnalyses.version,
        StandardAnalyses.script_used,
        StandardAnalyses.targets,
        StandardAnalyses.excluded,
        StandardAnalyses.values,
        StandardAnalyses.meta,
        StandardAnalyses.updated_at.label("analyzed_at"),
    ).where(StandardAnalyses.pitch_id == pitch_id)
    return _fetch(db, stmt)


def coaching_plans(db: Session, pitch_id: uuid.UUID, limit: int) -> list[dict[str, Any]]:
    stmt = (
        select(
            CoachingPlan.requested_at,
            CoachingPlan.source_take_id,
            CoachingPlan.script_version_id,
            CoachingPlan.target_ms,
            CoachingPlan.min_ms,
            CoachingPlan.max_ms,
            CoachingPlan.payload["fallback_reason"].astext.label("fallback_reason"),
            CoachingPlan.meta["model"].astext.label("model"),
            CoachingPlan.payload,
            CoachingPlan.meta,
        )
        .where(CoachingPlan.pitch_id == pitch_id)
        .order_by(CoachingPlan.requested_at.desc())
        .limit(limit)
    )
    return _fetch(db, stmt)


def _take_rows() -> Select[Any]:
    return (
        select(
            Take.id,
            Take.take_number,
            Take.pitch_id,
            Pitch.title.label("pitch_title"),
            User.name.label("user_name"),
            User.email.label("user_email"),
            Take.mode,
            Take.script_mode,
            Take.status,
            Take.end_reason,
            Take.created_at,
            Take.started_at,
            Take.ended_at,
            ELAPSED_SEC.label("elapsed_sec"),
            Take.goal_time_sec,
            Take.analysis_stage,
            Take.analysis_stage_at,
            Take.review_status,
            Take.analysis_hold_reason,
        )
        .join(Pitch, Pitch.id == Take.pitch_id)
        .join(User, User.id == Pitch.user_id)
    )


def _fetch(db: Session, stmt: Select[Any]) -> list[dict[str, Any]]:
    return [dict(row) for row in db.execute(stmt).mappings()]


def _count_total_and_since(
    db: Session, created_at: InstrumentedAttribute[datetime], since: datetime
) -> tuple[int, int]:
    total, recent = db.execute(
        select(func.count(), func.count().filter(created_at >= since))
    ).one()
    return int(total), int(recent)


def _count_per_day(
    db: Session, created_at: InstrumentedAttribute[datetime], since: datetime
) -> dict[date, int]:
    day = func.date(func.timezone(DAY_TIMEZONE, created_at))
    rows = db.execute(
        select(day, func.count()).where(created_at >= since).group_by(day)
    ).all()
    return {value: count for value, count in rows}


def _float_or_none(value: Any) -> float | None:
    # AVG · percentile 은 Decimal 을 돌려주고 대상이 없으면 NULL 이다
    return None if value is None else float(value)


def rating_summary(db: Session) -> tuple[int, float | None]:
    """(평점 수, 평균). 탈퇴 뒤 보관한 평점도 센다. 평점이 없으면 평균은 None."""
    count, avg = db.execute(
        select(func.count(ServiceFeedback.id), func.avg(ServiceFeedback.rating))
    ).one()
    return int(count), _float_or_none(avg)


def list_feedbacks(db: Session, *, limit: int, offset: int) -> list[dict[str, Any]]:
    stmt = select(
        ServiceFeedback.id,
        ServiceFeedback.user_id,
        ServiceFeedback.rating,
        ServiceFeedback.created_at,
        UserSuggestion.content.label("suggestion"),
        UserSuggestion.category,
    ).outerjoin(UserSuggestion, UserSuggestion.service_feedback_id == ServiceFeedback.id)
    return _fetch(
        db,
        stmt.order_by(ServiceFeedback.created_at.desc(), ServiceFeedback.id.desc())
        .offset(offset)
        .limit(limit + 1),
    )


def list_suggestions(
    db: Session,
    *,
    source: str | None,
    limit: int,
    offset: int,
    category: str | None = None,
    user_id: uuid.UUID | None = None,
) -> list[dict[str, Any]]:
    stmt = select(
        UserSuggestion.id,
        UserSuggestion.user_id,
        UserSuggestion.content,
        UserSuggestion.source,
        UserSuggestion.category,
        UserSuggestion.service_feedback_id,
        UserSuggestion.created_at,
    )
    if source is not None:
        stmt = stmt.where(UserSuggestion.source == source)
    if category is not None:
        stmt = stmt.where(UserSuggestion.category == category)
    if user_id is not None:
        stmt = stmt.where(UserSuggestion.user_id == user_id)
    return _fetch(
        db,
        stmt.order_by(UserSuggestion.created_at.desc(), UserSuggestion.id.desc())
        .offset(offset)
        .limit(limit + 1),
    )


# 작성자 묶음. 활성 사용자는 user_id 하나로 평점 · 팝업 의견 · 마이페이지 의견을 묶는다.
# 탈퇴하면 user_id 가 비어 누구 것인지 알 수 없다. 서로 다른 탈퇴자를 합치지 않게
# 평점(과 그 팝업 의견)은 평점 ID, 마이페이지 의견은 의견 ID 로 따로 묶는다
def _retained_id(
    user_id: ColumnElement[Any], feedback_id: ColumnElement[Any], suggestion_id: ColumnElement[Any]
) -> ColumnElement[Any]:
    return case((user_id.is_(None), func.coalesce(feedback_id, suggestion_id)))


def list_feedback_groups(db: Session, *, limit: int, offset: int) -> list[dict[str, Any]]:
    """작성자 묶음마다 평점(0~1개)과 의견 수. 마지막으로 제출한 묶음부터.

    평점과 의견을 한 목록으로 합친 뒤 묶음으로 GROUP BY 해서 페이지를 나눈다.
    JOIN 한 행으로 나누면 한 사용자의 의견이 페이지 경계에서 갈린다.
    """
    activity = union_all(
        select(
            ServiceFeedback.user_id,
            ServiceFeedback.id.label("feedback_id"),
            cast(null(), Uuid).label("suggestion_id"),
            ServiceFeedback.rating,
            ServiceFeedback.created_at,
        ),
        select(
            UserSuggestion.user_id,
            UserSuggestion.service_feedback_id,
            UserSuggestion.id,
            cast(null(), Integer),
            UserSuggestion.created_at,
        ),
    ).subquery()
    rated = activity.c.suggestion_id.is_(None)
    grouped = (
        select(
            activity.c.user_id,
            _retained_id(
                activity.c.user_id, activity.c.feedback_id, activity.c.suggestion_id
            ).label("retained_id"),
            func.max(activity.c.rating).label("rating"),
            func.max(activity.c.created_at).filter(rated).label("rated_at"),
            func.count(activity.c.suggestion_id).label("suggestion_count"),
            func.max(activity.c.created_at).label("last_at"),
        )
        .group_by(activity.c.user_id, "retained_id")
        .subquery()
    )
    return _fetch(
        db,
        select(grouped)
        .order_by(
            grouped.c.last_at.desc(),
            grouped.c.user_id.nulls_last(),
            grouped.c.retained_id,
        )
        .offset(offset)
        .limit(limit + 1),
    )


def list_group_suggestions(
    db: Session,
    *,
    user_ids: list[uuid.UUID],
    retained_ids: list[uuid.UUID],
    per_group: int,
) -> list[dict[str, Any]]:
    """묶음마다 최근 의견 `per_group` 개. 정렬은 의견 목록(`list_suggestions`)과 같다."""
    retained_id = _retained_id(
        UserSuggestion.user_id, UserSuggestion.service_feedback_id, UserSuggestion.id
    )
    ranked = (
        select(
            UserSuggestion.id,
            UserSuggestion.user_id,
            retained_id.label("retained_id"),
            UserSuggestion.content,
            UserSuggestion.source,
            UserSuggestion.category,
            UserSuggestion.created_at,
            func.row_number()
            .over(
                partition_by=(UserSuggestion.user_id, retained_id),
                order_by=(UserSuggestion.created_at.desc(), UserSuggestion.id.desc()),
            )
            .label("rank"),
        )
        .where(
            or_(
                UserSuggestion.user_id.in_(user_ids),
                and_(UserSuggestion.user_id.is_(None), retained_id.in_(retained_ids)),
            )
        )
        .subquery()
    )
    return _fetch(
        db,
        select(ranked)
        .where(ranked.c.rank <= per_group)
        .order_by(ranked.c.created_at.desc(), ranked.c.id.desc()),
    )
