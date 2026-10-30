import uuid
from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

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
    pitch_id: uuid.UUID
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
    analysis_stage: str | None
    analysis_stage_at: datetime | None
    review_status: str | None
    analysis_hold_reason: str | None


class AdminLiveSegment(BaseModel):
    seq: int
    stt_session_no: int
    start_ms: int
    end_ms: int
    transcript: str
    confidence: float
    speech_final: bool


class AdminLiveSecond(BaseModel):
    t_ms: int
    slide_number: int | None
    gaze: dict[str, Any] | None
    voice: dict[str, Any] | None
    stt_status: str


class AdminTakeLive(BaseModel):
    status: str
    segments: list[AdminLiveSegment]
    seconds: list[AdminLiveSecond]


class AdminAIResult(BaseModel):
    kind: str
    revision: int
    created_at: datetime
    replayed: bool | None
    criteria_changed: bool | None
    measured_ratios: dict[str, float | None]
    fallback_reason: str | None
    model: str | None
    payload: Any
    meta: Any | None


class AdminCoachEvent(BaseModel):
    seq: int
    t_ms: int
    kind: str
    event_id: str
    payload: Any
    meta: Any


class AdminTakeSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    average_cpm: float | None
    filler_count: int | None
    audience_gaze: int | None
    slide_gaze: int | None
    script_gaze: int | None
    script_dependency_count: int | None
    summary: str | None
    overall_confidence: float | None
    speak_accuracy: float | None
    volume: float | None
    total_intermission: int | None
    score: int | None


class AdminTakeResults(BaseModel):
    ai_results: list[AdminAIResult]
    coach_events: list[AdminCoachEvent]
    summary: AdminTakeSummary | None


class AdminSegmentWords(BaseModel):
    seq: int
    start_ms: int
    end_ms: int
    final_at_ms: int | None
    words: Any


class AdminSlideEvent(BaseModel):
    started_ms: int
    slide_number: int


class AdminCalibration(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    created_at: datetime
    face_detected: bool
    mic_detected: bool
    base_volume: float
    base_level_db: float | None
    gaze_confidence: float
    result: Any | None


class AdminTakeInputs(BaseModel):
    script_mode: str
    standard_version: int | None
    mission_ids: Any | None
    raw_data_consent: bool
    utterance_ends: Any | None
    slide_events: list[AdminSlideEvent]
    segments: list[AdminSegmentWords]
    calibration: AdminCalibration | None


class AdminExportResult(BaseModel):
    payload: Any
    meta: Any | None
    revision: int


class AdminTakeExport(BaseModel):
    take_id: uuid.UUID
    raw_data_consent: bool
    metric_second_count: int
    request: dict[str, Any] | None
    results: dict[str, AdminExportResult]


class AdminScriptDetail(BaseModel):
    version: int
    created_at: datetime
    parse_status: str
    parse_error: str | None
    parse_requested_at: datetime
    rubric_status: str | None
    rubric_requested_at: datetime | None
    rubric_retry_used: bool
    slide_count: int
    rubric_slide_count: int
    missing_rubric_slides: list[int]


class AdminStandardAnalysis(BaseModel):
    version: int
    criteria_count: int
    script_used: bool | None = None
    targets: Any | None = None
    excluded: Any | None = None
    values: Any | None = None
    meta: Any | None = None
    analyzed_at: datetime | None = None


class AdminCoachingPlan(BaseModel):
    requested_at: datetime
    source_take_id: uuid.UUID | None
    script_version_id: uuid.UUID | None
    target_ms: int | None
    min_ms: int | None
    max_ms: int | None
    fallback_reason: str | None
    model: str | None
    payload: Any
    meta: Any


class AdminPitchAI(BaseModel):
    scripts: list[AdminScriptDetail]
    standards: list[AdminStandardAnalysis]
    coaching_plans: list[AdminCoachingPlan]


class AdminTakeSurveyRow(BaseModel):
    id: uuid.UUID
    # 탈퇴했으면 user_id, 발표 · Take 를 지웠으면 take_id 이하가 비어 있다
    user_id: uuid.UUID | None
    take_id: uuid.UUID | None
    pitch_title: str | None
    take_number: int | None
    accuracy_rating: int
    usefulness_rating: int
    comment: str | None
    question_version: int
    created_at: datetime


class AdminTakeSurveySummary(BaseModel):
    # 문항 문구가 바뀌면 앞뒤 응답을 섞어 평균 내지 않도록 버전마다 따로 센다
    question_version: int
    survey_count: int
    avg_accuracy: float
    avg_usefulness: float


class AdminSuggestionRow(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID | None
    content: str
    source: str
    category: SuggestionCategory
    created_at: datetime


class AdminPage[T](BaseModel):
    items: list[T]
    next_offset: int | None
