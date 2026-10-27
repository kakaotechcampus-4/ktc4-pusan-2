"""관리자 콘솔 집계. 읽기만 하고 커밋하지 않는다."""

import uuid
from datetime import UTC, datetime, time, timedelta

from sqlalchemy.orm import Session

from pitch_coach_backend.module.admin import repository
from pitch_coach_backend.module.admin.dto import (
    AdminFeedbackGroupRow,
    AdminFeedbackRow,
    AdminGroupSuggestion,
    AdminPage,
    AdminSuggestionRow,
    AdminTakeRow,
    AdminUserRow,
    DailyCount,
    PracticeTimeStats,
    StatsResponse,
    TakeStats,
    UserStats,
)
from pitch_coach_backend.module.admin.repository import KST
from pitch_coach_backend.module.take.entity import Take
from pitch_coach_backend.module.take.service.lifecycle import ABANDON_AFTER_SEC

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
# 사용자별 평점 · 의견에서 한 행에 미리 싣는 의견 수. 마이페이지 의견은 수에 제한이 없다
GROUP_SUGGESTION_PREVIEW = 3


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


def _named(counts: dict[str | None, int]) -> dict[str, int]:
    # NOT NULL 컬럼이라 None 키는 없다
    return {key: n for key, n in counts.items() if key is not None}


def list_feedbacks(db: Session, *, limit: int, offset: int) -> AdminPage[AdminFeedbackRow]:
    rows = repository.list_feedbacks(db, limit=limit, offset=offset)
    return AdminPage[AdminFeedbackRow](
        items=[AdminFeedbackRow(**row) for row in rows[:limit]],
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


def list_feedback_groups(
    db: Session, *, limit: int, offset: int
) -> AdminPage[AdminFeedbackGroupRow]:
    groups = repository.list_feedback_groups(db, limit=limit, offset=offset)
    page = groups[:limit]
    suggestions: dict[tuple[uuid.UUID | None, uuid.UUID | None], list[AdminGroupSuggestion]] = {
        (group["user_id"], group["retained_id"]): [] for group in page
    }
    rows = repository.list_group_suggestions(
        db,
        user_ids=[group["user_id"] for group in page if group["user_id"] is not None],
        retained_ids=[group["retained_id"] for group in page if group["retained_id"] is not None],
        per_group=GROUP_SUGGESTION_PREVIEW,
    )
    for row in rows:
        suggestions[row["user_id"], row["retained_id"]].append(AdminGroupSuggestion(**row))
    return AdminPage[AdminFeedbackGroupRow](
        items=[
            AdminFeedbackGroupRow(
                **group, suggestions=suggestions[group["user_id"], group["retained_id"]]
            )
            for group in page
        ],
        next_offset=offset + limit if len(groups) > limit else None,
    )
