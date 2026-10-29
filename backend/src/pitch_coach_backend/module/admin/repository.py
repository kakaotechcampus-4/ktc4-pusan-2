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
from sqlalchemy.orm import InstrumentedAttribute, Session

from pitch_coach_backend.core.config import settings
from pitch_coach_backend.module.pitch.entity import Pitch
from pitch_coach_backend.module.service_feedback.entity import ServiceFeedback, UserSuggestion
from pitch_coach_backend.module.take.entity import Take
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

    시작 전(`READY`)은 넣지 않는다. 시작하기 전에 떠난 Take 는 정리하는 곳이 없어 계속 쌓인다.
    분석 중(`ANALYZING`)도 넣지 않는다. 아직 `COMPLETED` 로 바꾸는 곳이 없어
    끝난 Take 가 모두 여기 머문다.
    """
    active_at = last_active_at()
    stmt = _take_rows().where(Take.status == "RUNNING", active_at < active_before)
    return _fetch(db, stmt.order_by(active_at).limit(limit))


def _take_rows() -> Select[Any]:
    return (
        select(
            Take.id,
            Take.take_number,
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
