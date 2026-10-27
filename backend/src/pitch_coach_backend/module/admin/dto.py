import uuid
from datetime import date, datetime

from pydantic import BaseModel


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
    created_at: datetime


class AdminSuggestionRow(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID | None
    content: str
    source: str
    service_feedback_id: uuid.UUID | None
    created_at: datetime


class AdminPage[T](BaseModel):
    items: list[T]
    next_offset: int | None
