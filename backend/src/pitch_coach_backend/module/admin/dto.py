import uuid
from datetime import date, datetime

from pydantic import BaseModel

from pitch_coach_backend.module.service_feedback.enums import SuggestionCategory


class UserStats(BaseModel):
    total: int
    today: int


class TakeStats(BaseModel):
    total: int
    today: int
    # 값별 개수. 0 인 값은 빠진다
    by_status: dict[str, int]
    by_mode: dict[str, int]
    by_script_mode: dict[str, int]
    # 끝난 Take 만 센다. 사용자가 직접 끝냈으면 USER
    by_end_reason: dict[str, int]


class PracticeTimeStats(BaseModel):
    """연습 시간. 시작 · 끝 시각이 다 있는 Take 만 센다."""

    measured_takes: int
    total_sec: float
    avg_sec: float | None
    median_sec: float | None
    # 실제 연습 시간 ÷ 목표 시간의 평균. 1 보다 크면 목표보다 길게 했다
    avg_goal_ratio: float | None


class DailyCount(BaseModel):
    date: date
    signups: int
    takes: int


class StatsResponse(BaseModel):
    users: UserStats
    takes: TakeStats
    practice_time: PracticeTimeStats
    # 오늘까지 최근 며칠. 날짜는 한국 시간 기준이고 빈 날도 0 으로 들어 있다
    daily: list[DailyCount]


class AdminUserRow(BaseModel):
    id: uuid.UUID
    name: str
    email: str
    created_at: datetime
    pitch_count: int
    take_count: int
    total_practice_sec: float
    last_take_at: datetime | None


class AdminTakeRow(BaseModel):
    id: uuid.UUID
    take_number: int
    pitch_title: str
    user_name: str
    user_email: str
    mode: str
    script_mode: str
    status: str
    end_reason: str | None
    created_at: datetime
    started_at: datetime | None
    ended_at: datetime | None
    elapsed_sec: float | None
    goal_time_sec: int


class AdminFeedbackRow(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID | None
    rating: int
    suggestion: str | None
    category: SuggestionCategory | None
    created_at: datetime


class AdminSuggestionRow(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID | None
    content: str
    source: str
    category: SuggestionCategory
    service_feedback_id: uuid.UUID | None
    created_at: datetime


class AdminRatingSummary(BaseModel):
    # 평점을 남긴 수만 센다. 의견만 보낸 미평가 사용자는 빠진다
    rated_count: int
    avg_rating: float | None


class AdminGroupSuggestion(BaseModel):
    id: uuid.UUID
    content: str
    source: str
    category: SuggestionCategory
    created_at: datetime


class AdminFeedbackGroupRow(BaseModel):
    """한 사용자의 평점과 의견. 탈퇴했으면 user_id 가 비고 retained_id 로 묶음을 가른다."""

    user_id: uuid.UUID | None
    # 탈퇴해 작성자를 알 수 없는 묶음의 ID. 평점이 있으면 평점 ID, 없으면 마이페이지 의견 ID 다
    retained_id: uuid.UUID | None
    rating: int | None
    rated_at: datetime | None
    suggestion_count: int
    # 최근 의견부터 몇 개만. 나머지는 의견 목록을 user_id 로 걸러 이어서 받는다
    suggestions: list[AdminGroupSuggestion]
    last_at: datetime


class AdminPage[T](BaseModel):
    items: list[T]
    next_offset: int | None
