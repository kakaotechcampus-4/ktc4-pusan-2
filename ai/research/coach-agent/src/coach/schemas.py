"""BE ↔ 코치 계약 (pydantic).

이 파일이 요청 · 응답 · 이벤트 · Take 결과 모양의 단일 진실 원천입니다.

입력 모델은 모르는 필드를 무시합니다(extra="ignore") — BE 가 필드를 먼저 추가해도 깨지지 않게.
필드 이름은 snake_case 입니다 (BE DTO · 리뷰 DTO 와 같게).
"""

from __future__ import annotations

import logging
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from .version import FEATURE_VERSION, SCHEMA_VERSION
from .vocab import (
    Action,
    CandidateStatus,
    FeedbackType,
    Instruction,
    Issue,
    Mode,
    Outcome,
    StrategyChange,
)

#: 계약 상수(features/coach-agent.md 5-2). config.py 에 두지 않는다 — 설정 해시(판정 기준 버전)가
#: 바뀌면 안 된다
#: t_ms 상한: Take 최대 1시간 + 60초. evaluate 와 finalize 가 함께 쓴다
MAX_T_MS = 3_660_000
#: 지난 처리 시각보다 이만큼 넘게 앞선 요청은 처리하지 않는다(T_MS_JUMP)
MAX_T_MS_JUMP = 60_000
#: plan 요청 입력 크기 상한(5-2). plan 요청에만 건다 — evaluate · finalize 의 같은 필드에는 걸지
#: 않는다(거절하면 그 Take 의 코칭이 멈춘다). description 은 Python len(코드 포인트 수)
PLAN_MAX_SLIDES = 100
PLAN_MAX_MISSIONS = 20
PLAN_MAX_RECURRING_ISSUES = 20
PLAN_MAX_MISSION_DESCRIPTION = 500
#: evaluate · finalize 원자료 배열 한도(5-2 '입력 한도'). 정상 흐름에서는 닿지 않는 정책상 한도다:
#: 창 30초 기록 30개 · 60초 단어 수백 개의 몇 배. 넘으면 요청 전체가 422 다
MAX_WINDOW_RECORDS = 120
MAX_WINDOW_WORDS = 1_000
MAX_WINDOW_UTTERANCE_ENDS = 300
#: 단어 하나: 글자 수와 길이(끝 − 시작). 넘는 단어는 그 단어만 버린다. 길이 상한은 늦게 확정된
#: 단어가 말한 시각의 장을 찾도록 장 전환 기록을 남겨 두는 여유와 같은 값이다(judges.py)
MAX_WORD_CHARS = 50
MAX_WORD_SPAN_MS = 10_000
MAX_FINALIZE_EVENTS = 20_000
#: replay(Take 전체 원자료) 배열 한도. 1초 기록은 같은 t_ms 를 하나로 센 수다(한 시간 + 60초는
#: 3,660개). 단어 · 문장 끝은 60초 창 한도를 Take 최대 길이(61분)만큼 늘린 값이다 — 창으로는
#: 받는 발표를 복구에서 거절하지 않게
MAX_REPLAY_RECORDS = 4_000
MAX_REPLAY_WORDS = MAX_WINDOW_WORDS * 61
MAX_REPLAY_UTTERANCE_ENDS = MAX_WINDOW_UTTERANCE_ENDS * 61
MAX_REPLAY_CHANGES = 5_000
#: STT 상태. 모르는 값은 믿지 않는 상태(degraded)로 읽는다
STT_STATUSES = frozenset({"connecting", "ok", "reconnecting", "degraded", "closed"})

log = logging.getLogger(__name__)


#: by_t_ms 로 셀 때 배열 전체 길이의 상한(한도의 배수). 같은 1초가 되풀이돼도 받되, 버릴 기록이
#: 끝없이 와서 검사 비용이 커지지 않게 한다
REPEAT_ALLOWANCE = 4


def _t_key(item: Any) -> int | None:
    """같은 1초를 하나로 셀 때의 키. 읽을 수 없는 t_ms 면 None(그 기록은 버리므로 안 센다)."""
    t = item.get("t_ms") if isinstance(item, dict) else None
    return t if isinstance(t, int) and not isinstance(t, bool) else None


def _too_many(raw: Any, limit: int, name: str, *, by_t_ms: bool = False) -> None:
    """배열이 한도를 넘으면 ValueError(→ 422).

    by_t_ms 면 읽을 수 있는 t_ms 를 같은 것끼리 하나로 센 수를 한도와 비교하고, 배열 전체 길이는
    한도 × REPEAT_ALLOWANCE 까지 받는다.
    """
    if not isinstance(raw, list):
        return
    if by_t_ms:
        if len(raw) > limit * REPEAT_ALLOWANCE:
            raise ValueError(f"{name} 는 {limit * REPEAT_ALLOWANCE}개 이하여야 한다")
        count = len({_t_key(item) for item in raw} - {None}) if len(raw) > limit else len(raw)
    else:
        count = len(raw)
    if count > limit:
        raise ValueError(f"{name} 는 {limit}개 이하여야 한다")


def _readable(raw: Any, model: type[BaseModel], name: str, *, first_per_t_ms: bool = False) -> Any:
    """읽을 수 없는 항목(타입 · 범위가 틀림)은 그 항목만 버린다(gaze-tracking.md 5-2와 같은 규칙).

    항목 하나 때문에 요청 전체가 422 가 되어 그 1초의 모든 판정을 잃지 않게 하기 위해서다.
    first_per_t_ms 면 같은 t_ms 는 먼저 온 것만 남긴다(replay — 복구가 만드는 1초 요청이 창 한도에
    걸리지 않게. 판정 모듈도 먼저 온 것만 쓰므로 결과는 같다). 로그에는 버린 수만 남긴다.
    """
    if not isinstance(raw, list):
        return raw
    kept = []
    seen: set[int] = set()
    for item in raw:
        try:
            value = model.model_validate(item)
        except ValidationError:
            continue
        if first_per_t_ms:
            t = value.t_ms  # type: ignore[attr-defined]
            if t in seen:
                continue
            seen.add(t)
        kept.append(value)
    if len(kept) < len(raw):
        log.warning("dropped unreadable or repeated %s count=%d", name, len(raw) - len(kept))
    return kept


def _readable_ints(raw: Any, name: str) -> Any:
    """정수가 아니거나 음수인 시각은 그 값만 버린다."""
    if not isinstance(raw, list):
        return raw
    kept = [v for v in raw if isinstance(v, int) and not isinstance(v, bool) and v >= 0]
    if len(kept) < len(raw):
        log.warning("dropped unreadable %s count=%d", name, len(raw) - len(kept))
    return kept


def _stt_status(value: Any) -> Any:
    """모르는 STT 상태 · null · 문자열이 아닌 값은 degraded(믿지 않음).

    판정 모듈은 다섯 값만 받는다.
    """
    return value if isinstance(value, str) and value in STT_STATUSES else "degraded"


class _In(BaseModel):
    model_config = ConfigDict(extra="ignore")


class _Out(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ══════════════════════════════════════════════════════════════════════════
# 요청 — POST /coach/evaluate (1초마다)
# ══════════════════════════════════════════════════════════════════════════


class SlidePlan(_In):
    """대본 분석이 주는 장별 계획."""

    slide_number: int
    #: 0 이면 그 장의 장별 문제(SLIDE_OVER)를 내지 않는다
    target_ms: int = Field(ge=0)
    #: 이 장 대본 글자 수 (공백 제외). 진행도 = 이 장에서 말한 글자 수 / script_chars
    script_chars: int = Field(default=0, ge=0)


class Plan(_In):
    """발표 계획. Take 동안 바뀌지 않는다."""

    target_ms: int | None = Field(default=None, gt=0)
    #: 허용 범위. 예상 종료가 min_ms 보다 이르면 '너무 빨리 끝남', max_ms 를 넘으면 시간 초과
    min_ms: int | None = None
    max_ms: int | None = None
    slides: list[SlidePlan] = Field(default_factory=list)


class MissionTarget(_In):
    #: 지표 이름. 판정 모듈 summarize 의 지표 이름과 같다 (장 단위 시간은 slide_duration_ms)
    metric: str
    operator: Literal["LT", "LTE", "GT", "GTE"]
    value: float


class Mission(_In):
    """직전 리뷰의 next_missions 그대로."""

    mission_id: str
    area: FeedbackType
    slide_number: int | None = None
    priority: int = 1
    description: str | None = None
    target: MissionTarget | None = None


class RecurringIssue(_In):
    area: FeedbackType
    slide_number: int | None = None


class GazeRecordIn(_In):
    """FE 가 보내는 시선 1초 기록(#153)."""

    t_ms: int = Field(ge=0)
    duration_ms: int = Field(default=1000, gt=0)
    state: str
    direction: str | None = None
    confidence: float | None = None
    reliability: float | None = None
    issues: list[str] = Field(default_factory=list)
    frames: int | None = None

    @field_validator("issues", mode="before")
    @classmethod
    def _issues(cls, v: Any) -> Any:
        # null 이면 빈 목록, 문자열이 아닌 값은 버린다(gaze-tracking.md 5-2)
        if v is None:
            return []
        return [i for i in v if isinstance(i, str)] if isinstance(v, list) else v


class VoiceRecordIn(_In):
    """FE 가 보내는 음량 1초 기록(#155). 시선 판정도 같은 1초들의 voiced_ms 를 쓴다."""

    t_ms: int = Field(ge=0)
    duration_ms: int = Field(default=1000, gt=0)
    level_db: float | None = None
    voiced_ms: int = Field(default=0, ge=0)
    silence_ms: int = Field(default=0, ge=0)
    audio_live: bool = True

    @model_validator(mode="after")
    def _within_duration(self) -> VoiceRecordIn:
        if self.voiced_ms > self.duration_ms:
            raise ValueError("voiced_ms 는 duration_ms 이하여야 한다")
        return self


class WordIn(_In):
    """STT 확정 단어."""

    word: str = Field(max_length=MAX_WORD_CHARS)
    start_ms: int = Field(ge=0)
    end_ms: int = Field(ge=0)

    @model_validator(mode="after")
    def _span(self) -> WordIn:
        if not 0 <= self.end_ms - self.start_ms <= MAX_WORD_SPAN_MS:
            raise ValueError("단어는 시작 ≤ 끝이고 길이가 10초 이하여야 한다")
        return self


class SlideNow(_In):
    """지금 장과 그 장이 시작된 시각."""

    number: int
    started_ms: int


class CoachInputs(_In):
    """판정 모듈에 넘길 원자료. 창 길이는 judges.py 의 GAZE_VOICE_WINDOW_MS · WORDS_WINDOW_MS."""

    gaze_records: list[GazeRecordIn] = Field(default_factory=list)
    voice_records: list[VoiceRecordIn] = Field(default_factory=list)
    words: list[WordIn] = Field(default_factory=list)
    utterance_ends: list[int] = Field(default_factory=list)
    stt_status: str = "ok"
    slide: SlideNow | None = None

    # 배열이 한도를 넘으면 요청 전체를 거절하고(422), 한도 안이면 읽을 수 없는 항목만 버린다
    @field_validator("gaze_records", mode="before")
    @classmethod
    def _gaze(cls, v: Any) -> Any:
        _too_many(v, MAX_WINDOW_RECORDS, "inputs.gaze_records")
        return _readable(v, GazeRecordIn, "gaze_records")

    @field_validator("voice_records", mode="before")
    @classmethod
    def _voice(cls, v: Any) -> Any:
        _too_many(v, MAX_WINDOW_RECORDS, "inputs.voice_records")
        return _readable(v, VoiceRecordIn, "voice_records")

    @field_validator("words", mode="before")
    @classmethod
    def _words(cls, v: Any) -> Any:
        _too_many(v, MAX_WINDOW_WORDS, "inputs.words")
        return _readable(v, WordIn, "words")

    @field_validator("utterance_ends", mode="before")
    @classmethod
    def _ends(cls, v: Any) -> Any:
        _too_many(v, MAX_WINDOW_UTTERANCE_ENDS, "inputs.utterance_ends")
        return _readable_ints(v, "utterance_ends")

    @field_validator("stt_status", mode="before")
    @classmethod
    def _stt(cls, v: Any) -> Any:
        return _stt_status(v)


class Calibration(_In):
    """Take 밖에서 잡아 둔 값. base_level_db 가 null 이면 코치가 Take 첫 발화로 잡는다."""

    base_level_db: float | None = None


class CoachRequest(_In):
    take_id: str
    #: Take 시작 기준 경과 ms. 모든 시각이 이 시간축이다
    t_ms: int = Field(ge=0, le=MAX_T_MS)
    mode: Mode = Mode.COACHING
    plan: Plan = Field(default_factory=Plan)
    missions: list[Mission] = Field(default_factory=list)
    #: 직전 리뷰의 반복 문제
    recurring_issues: list[RecurringIssue] = Field(default_factory=list)
    #: 코칭 계획. null 이면 계획 없음. why 는 와도 쓰지 않는다
    coaching_plan: CoachingPlan | None = None
    #: 사용자 평가 기준의 대본 사용 설정. true 면 대본 응시를 지적하지 않는다 (SCRIPT_ALLOWED)
    script_used: bool | None = None
    #: 판정 모듈에 넘길 원자료
    inputs: CoachInputs = Field(default_factory=CoachInputs)
    calibration: Calibration = Field(default_factory=Calibration)
    #: 지난 응답의 coach_state 그대로. 첫 요청이면 null. BE 는 내용을 몰라도 된다
    coach_state: dict[str, Any] | None = None


# ══════════════════════════════════════════════════════════════════════════
# 코칭 계획 — v1.1 까지는 기본값, v1.2 에서 LLM 이 Take 시작 전에 만든다
# ══════════════════════════════════════════════════════════════════════════


class FocusItem(_In):
    area: FeedbackType
    slide_number: int | None = None
    #: config.policy.plan_weight_min ~ max 로 잘린다
    weight: float = 1.0
    why: str = ""


class RelaxItem(_In):
    area: FeedbackType
    slide_number: int | None = None
    why: str = ""


class CoachingPlan(_In):
    focus: list[FocusItem] = Field(default_factory=list)
    relax: list[RelaxItem] = Field(default_factory=list)
    max_interventions: int | None = Field(default=None, ge=0)


class SlideScript(_In):
    """장 하나의 대본. 계획을 세울 때만 쓴다 (1초 판단에는 글자 수만 쓴다)."""

    slide_number: int
    script: str = ""


class PlanMission(Mission):
    """plan 요청의 미션. 공유 Mission 에 설명 길이 상한만 더한다(evaluate 에는 걸지 않는다)."""

    description: str | None = Field(default=None, max_length=PLAN_MAX_MISSION_DESCRIPTION)


class PlanRequest(_In):
    """Take 시작 전 코칭 계획 요청. 바탕 id · 발표 계획 · 장별 대본 · 미션 · 반복 문제 · 직전 Take
    결과 · 직전 리뷰.

    요청에 모드가 없다 — 계획은 Take 가 시작되기 전에 만들고, 실전 모드 Take 는 evaluate 에서
    말하지 않으므로 계획이 영향을 주지 않는다. 옛 필드(take_id · mode · memory)가 와도 무시한다.
    """

    pitch_id: str
    #: 이 계획의 바탕이 된 Take · 대본 버전. 응답에 그대로 돌려준다(BE 의 계획 고르기용)
    source_take_id: str | None = None
    script_version_id: str | None = None
    plan: Plan = Field(default_factory=Plan)
    scripts: list[SlideScript] = Field(default_factory=list, max_length=PLAN_MAX_SLIDES)
    missions: list[PlanMission] = Field(default_factory=list, max_length=PLAN_MAX_MISSIONS)
    recurring_issues: list[RecurringIssue] = Field(
        default_factory=list, max_length=PLAN_MAX_RECURRING_ISSUES
    )
    #: 직전 Take 결과(take_result)와 직전 리뷰 출력 그대로. 크기를 묶지 않는 dict 다 — 개입 수 ·
    #: 효과 · 포기한 문제는 코치가 여기서 직접 센다
    previous_take_result: dict[str, Any] | None = None
    previous_review: dict[str, Any] | None = None

    @model_validator(mode="after")
    def _limit_plan_slides(self) -> PlanRequest:
        if len(self.plan.slides) > PLAN_MAX_SLIDES:
            raise ValueError(f"plan.slides 는 {PLAN_MAX_SLIDES}개까지")
        return self


class PlanFocusDraft(BaseModel):
    """LLM 출력 — 집중할 영역. 출력 스키마는 프롬프트와 함께 계획 해시에 들어간다."""

    type: FeedbackType
    slide_number: int | None = Field(description="장 번호. Take 전체면 null")
    weight: float = Field(description="우선순위 가중치 1.2 ~ 2.0. 클수록 먼저 챙긴다")
    why: str = Field(description="대본 · 미션 · 이전 리뷰에서 찾은 짧은 근거 한 문장")


class PlanRelaxDraft(BaseModel):
    """LLM 출력 — 이 장에서는 지적하지 않아도 되는 영역."""

    type: FeedbackType
    slide_number: int = Field(description="장 번호. 장 단위로만 봐줄 수 있다")
    why: str = Field(description="대본에서 찾은 짧은 근거 한 문장")


class PlanDraft(BaseModel):
    """LLM 이 내는 코칭 계획 초안. 코치가 검증하고 값을 잘라 CoachingPlan 으로 만든다."""

    focus: list[PlanFocusDraft]
    relax: list[PlanRelaxDraft]
    max_interventions: int | None = Field(
        description="이번 Take 에서 말할 최대 횟수. 제한할 이유가 없으면 null"
    )


PlanFallbackReason = Literal["LLM_TIMEOUT", "LLM_ERROR", "LLM_INVALID_OUTPUT", "LLM_UNAVAILABLE"]


class PlanMeta(_Out):
    """plan 응답을 만든 버전. 판정이 없어 criteria_versions 가 없다(architecture 5절)."""

    schema_version: str = SCHEMA_VERSION
    feature_version: str = FEATURE_VERSION
    #: 응답한 모델. 기본 계획이면 LLM 이 답했어도 null
    model: str | None = None


class PlanResponse(_Out):
    coaching_plan: CoachingPlan
    #: 요청 값 그대로(BE 의 계획 고르기용)
    source_take_id: str | None
    script_version_id: str | None
    #: 기본 계획으로 돌아간 이유. LLM 계획을 썼으면 null
    fallback_reason: PlanFallbackReason | None = None
    #: 검증에서 뺀 항목과 이유 (예: "relax TIME 3: 봐줄 수 없는 영역")
    dropped: list[str] = Field(default_factory=list)
    #: 프롬프트 · 출력 스키마 · 요청한 모델 이름으로 만든 해시. 같은 해시 · 같은 입력이면 같은
    #: 질문이다
    planner_hash: str
    meta: PlanMeta


# ══════════════════════════════════════════════════════════════════════════
# 응답
# ══════════════════════════════════════════════════════════════════════════


class Feedback(_Out):
    area: FeedbackType
    instruction: Instruction
    message: str
    priority: int = Field(ge=0, le=100)
    confidence: float = Field(ge=0.0, le=1.0)
    #: start_ms · end_ms · slide_number + 모듈이 준 값
    evidence: dict[str, Any]


# ── 이벤트 — BE 가 그대로 쌓아 두는 기록. 리뷰 에이전트 근거의 원천 ──────────


class _Event(_Out):
    event_id: str
    t_ms: int


class InterventionEvent(_Event):
    kind: Literal["INTERVENTION"] = "INTERVENTION"
    #: event_id 와 같은 값 (효과 · 사다리 이벤트가 가리킨다)
    intervention_id: str
    issue_type: Issue
    area: FeedbackType
    instruction: Instruction
    message: str
    priority: int
    confidence: float
    reason_codes: list[str]
    slide_number: int | None = None
    #: 같은 응답 feedback.evidence 와 같은 값 — 문제 구간 start_ms · end_ms · slide_number 와 판정
    #: 모듈이 준 근거. 기본값은 이 필드가 없던 옛 이벤트를 finalize 가 받을 수 있게 한다
    evidence: dict[str, Any] = Field(default_factory=dict)


class OutcomeEvent(_Event):
    """개입 몇 초 뒤 실제로 행동이 바뀌었는가."""

    kind: Literal["OUTCOME"] = "OUTCOME"
    intervention_id: str
    outcome: Outcome
    metric: str | None = None
    before: float | None = None
    after: float | None = None


class EpisodeEvent(_Event):
    """문제 구간 하나가 끝났다. 개입했든 안 했든 남긴다 — 리뷰의 '문제 구간'이 된다."""

    kind: Literal["EPISODE"] = "EPISODE"
    issue_type: Issue
    area: FeedbackType
    slide_number: int | None = None
    start_ms: int
    end_ms: int
    peak_severity: float
    intervention_ids: list[str] = Field(default_factory=list)
    #: 이 구간에서 말하지 못한 이유들 (EXAM_MODE, MIN_GAP …)
    suppressed_reasons: list[str] = Field(default_factory=list)
    #: 센서를 믿을 수 있던 / 없던 시간. 대부분 믿을 수 없었다면 리뷰는 이 구간을 문제로 말하지
    #: 않는다
    reliable_ms: int = 0
    unreliable_ms: int = 0
    #: 구간 평균 심각도. 부담(심각도 × 초)은 이걸로 잰다 — 최고값은 잡음에 크게 흔들린다
    mean_severity: float = 0.0


class SlideEvent(_Event):
    """한 장 방문의 영역별 합계. 장을 떠나고 그 장의 단어가 다 확정되면(또는 Take 가 끝나면) 하나.

    다시 돌아온 장은 방문마다 하나씩 낸다. 평균은 합계에서 모듈의 summarize 로 낸다.
    """

    kind: Literal["SLIDE"] = "SLIDE"
    slide_number: int
    start_ms: int
    end_ms: int
    target_ms: int | None = None
    #: 영역(GAZE · SPEED · VOLUME · PAUSE · FILLER · TIME) → 이름 → 이 방문의 합
    tally: dict[str, dict[str, float]] = Field(default_factory=dict)


class SuppressedEvent(_Event):
    """걸렸지만 말하지 않은 기록. 같은 문제는 suppress_log_gap_ms 에 한 번만 남는다."""

    kind: Literal["SUPPRESSED"] = "SUPPRESSED"
    issue_type: Issue
    area: FeedbackType
    slide_number: int | None = None
    instruction: Instruction
    status: CandidateStatus
    priority: int
    reasons: list[str]


class StrategyEvent(_Event):
    """효과가 없어 방법을 바꿨거나(ESCALATED) 그만뒀다(GAVE_UP)."""

    kind: Literal["STRATEGY"] = "STRATEGY"
    issue_type: Issue
    area: FeedbackType
    slide_number: int | None = None
    change: StrategyChange
    from_instruction: Instruction
    from_variant: str
    to_instruction: Instruction | None = None
    to_variant: str | None = None
    intervention_id: str


CoachEvent = Annotated[
    InterventionEvent | OutcomeEvent | EpisodeEvent | SuppressedEvent | StrategyEvent | SlideEvent,
    Field(discriminator="kind"),
]


class Meta(_Out):
    """응답을 만든 버전. BE 가 결과와 함께 저장한다."""

    schema_version: str = SCHEMA_VERSION
    feature_version: str = FEATURE_VERSION
    #: 모듈 이름(gaze · pace · volume · filler · timing) → criteria_version.
    #: coach 는 `<feature_version>+<설정 해시>`
    criteria_versions: dict[str, str]
    #: LLM 을 쓰지 않으므로 null
    model: str | None = None


class CoachResponse(_Out):
    action: Action
    #: INTERVENE 일 때만
    feedback: Feedback | None = None
    #: 영역(GAZE · SPEED · VOLUME · PAUSE · FILLER · TIME) → 판정 모듈이 준 상태. 읽지 않아도 되는
    #: 상태 표시라 여러 개를 함께 띄워도 된다
    indicators: dict[str, str] = Field(default_factory=dict)
    reason_codes: list[str] = Field(default_factory=list)
    events: list[CoachEvent] = Field(default_factory=list)
    #: 다음 요청에 그대로 붙인다
    coach_state: dict[str, Any]
    meta: Meta


# ══════════════════════════════════════════════════════════════════════════
# Take 종료 — 열린 구간을 닫는다
# ══════════════════════════════════════════════════════════════════════════


class ReplayWord(WordIn):
    """Take 전체 원자료의 확정 단어. 확정된 시각이 지난 요청에만 싣는다."""

    final_at_ms: int


class SttStatusChange(_In):
    """STT 상태가 바뀐 시각과 바뀐 상태."""

    t_ms: int = Field(ge=0)
    status: str

    @field_validator("status", mode="before")
    @classmethod
    def _stt(cls, v: Any) -> Any:
        return _stt_status(v)


class ReplayInput(_In):
    """Take 전체 원자료. 409 REPLAY_REQUIRED 를 받았거나 coach_state 를 잃었을 때만 싣는다.

    코치는 이것으로 BE 가 1초마다 보냈을 /coach/evaluate 요청을 다시 만들어 처음부터 판정한다.
    """

    gaze_records: list[GazeRecordIn] = Field(default_factory=list)
    voice_records: list[VoiceRecordIn] = Field(default_factory=list)
    stt_status_changes: list[SttStatusChange] = Field(default_factory=list)
    words: list[ReplayWord] = Field(default_factory=list)
    utterance_ends: list[int] = Field(default_factory=list)
    slides: list[SlideNow] = Field(default_factory=list)

    @field_validator("gaze_records", mode="before")
    @classmethod
    def _gaze(cls, v: Any) -> Any:
        _too_many(v, MAX_REPLAY_RECORDS, "replay.gaze_records", by_t_ms=True)
        return _readable(v, GazeRecordIn, "replay.gaze_records", first_per_t_ms=True)

    @field_validator("voice_records", mode="before")
    @classmethod
    def _voice(cls, v: Any) -> Any:
        _too_many(v, MAX_REPLAY_RECORDS, "replay.voice_records", by_t_ms=True)
        return _readable(v, VoiceRecordIn, "replay.voice_records", first_per_t_ms=True)

    @field_validator("words", mode="before")
    @classmethod
    def _words(cls, v: Any) -> Any:
        _too_many(v, MAX_REPLAY_WORDS, "replay.words")
        return _readable(v, ReplayWord, "replay.words")

    @field_validator("utterance_ends", mode="before")
    @classmethod
    def _ends(cls, v: Any) -> Any:
        _too_many(v, MAX_REPLAY_UTTERANCE_ENDS, "replay.utterance_ends")
        return _readable_ints(v, "replay.utterance_ends")

    @field_validator("stt_status_changes", mode="before")
    @classmethod
    def _changes(cls, v: Any) -> Any:
        _too_many(v, MAX_REPLAY_CHANGES, "replay.stt_status_changes")
        return _readable(v, SttStatusChange, "replay.stt_status_changes")

    @field_validator("slides", mode="before")
    @classmethod
    def _slides(cls, v: Any) -> Any:
        _too_many(v, MAX_REPLAY_CHANGES, "replay.slides")
        return _readable(v, SlideNow, "replay.slides")


class FinalizeRequest(_In):
    """Take 종료. /coach/evaluate 와 같은 Take 상수에 마지막 창 · 이벤트 · coach_state 를 더한다."""

    schema_version: str = SCHEMA_VERSION
    take_id: str
    #: Take 가 끝난 시각
    t_ms: int = Field(ge=0, le=MAX_T_MS)
    mode: Mode = Mode.COACHING
    plan: Plan = Field(default_factory=Plan)
    missions: list[Mission] = Field(default_factory=list)
    recurring_issues: list[RecurringIssue] = Field(default_factory=list)
    coaching_plan: CoachingPlan | None = None
    script_used: bool | None = None
    calibration: Calibration = Field(default_factory=Calibration)
    #: 대본 보기. 판정은 바꾸지 않고 Take 결과에 남긴다
    script_mode: Literal["HIGHLIGHT", "KEYWORD", "OFF"] | None = None
    #: 마지막 응답의 coach_state. 잃었으면 null
    coach_state: dict[str, Any] | None = None
    #: 이 Take 에서 BE 가 쌓은 코치 이벤트 전부(받은 순서대로)
    events: list[CoachEvent] = Field(default_factory=list)

    @field_validator("events", mode="before")
    @classmethod
    def _events(cls, v: Any) -> Any:
        _too_many(v, MAX_FINALIZE_EVENTS, "events")
        return v

    #: 마지막 창. /coach/evaluate 의 inputs 와 같은 모양
    inputs: CoachInputs = Field(default_factory=CoachInputs)
    #: Take 전체 원자료. 있으면 처음부터 다시 판정해 Take 결과를 만든다(replayed)
    replay: ReplayInput | None = None


class SlideResult(_Out):
    """장 하나의 값. 다시 온 장은 합친다. start_ms · end_ms 는 첫 방문 시작 · 마지막 방문 끝."""

    slide_number: int
    start_ms: int | None = None
    end_ms: int | None = None
    target_ms: int | None = None
    measured_ratio: float | None = None
    #: 영역 모듈의 summarize 결과(measured_ratio 제외). 측정 비율이 기준 미만이면 null
    metrics: dict[str, Any] | None = None


class AreaResult(_Out):
    measured_ratio: float | None = None
    #: 영역 모듈의 summarize 결과(measured_ratio 제외, 묶음 값 포함). 기준 미만이면 null
    take: dict[str, Any] | None = None
    slides: list[SlideResult] | None = None
    unmeasured_reason: Literal["LOW_MEASURED_RATIO"] | None = None


class AreaCriteria(_Out):
    """영역 하나의 판정 기준: 모듈의 기준 버전과 그 영역 문제의 criteria()."""

    criteria_version: str | None = None
    issues: dict[str, IssueCriteria] = Field(default_factory=dict)


class ProblemSegment(_Out):
    """문제 구간 하나. 같은 문제의 끊긴 조각은 합치고, 판정 지연만큼 되돌려 Take 안으로 자른다."""

    area: FeedbackType
    issue_type: Issue
    #: null 이면 Take 전체에 걸친 문제
    slide_number: int | None = None
    start_ms: int
    end_ms: int
    #: 구간 평균 심각도(0~1)
    mean_severity: float
    #: 센서를 믿을 수 있던 시간 비율이 기준 이상이면 true
    reliable: bool
    #: 이 구간에서 말을 걸었는가
    coached: bool


class TakeIntervention(_Out):
    """말을 건 기록 하나와 그 효과."""

    intervention_id: str
    t_ms: int
    area: FeedbackType
    issue_type: Issue
    instruction: Instruction
    message: str
    #: 효과를 재지 못했거나 아직 안 쟀으면 null
    outcome: Outcome | None = None
    metric: str | None = None
    before: float | None = None
    after: float | None = None


class GaveUp(_Out):
    """효과가 없어 더 말하지 않기로 한 문제."""

    issue_type: Issue
    slide_number: int | None = None


class TakePlan(_Out):
    """이 Take 를 판정한 시간 계획 — 요청 plan 에서 장 목록을 뺀 것. 리뷰가 시간 지표의 좋은 방향을
    정할 때 쓴다(장 목표는 areas.TIME.slides 에 있다)."""

    target_ms: int | None = None
    min_ms: int | None = None
    max_ms: int | None = None


class TakeResult(_Out):
    """Take 의 사실(지표 · 판정 기준). 결론은 담지 않는다."""

    take_id: str
    duration_ms: int
    script_mode: Literal["HIGHLIGHT", "KEYWORD", "OFF"] | None = None
    #: 요청에 plan 이 실렸을 때만. 없으면 null
    plan: TakePlan | None = None
    replayed: bool = False
    criteria_changed: bool = False
    #: 영역(GAZE · SPEED · VOLUME · PAUSE · FILLER · TIME) → 값
    areas: dict[str, AreaResult]
    problem_segments: list[ProblemSegment] = Field(default_factory=list)
    interventions: list[TakeIntervention] = Field(default_factory=list)
    gave_up: list[GaveUp] = Field(default_factory=list)
    criteria: dict[str, AreaCriteria]


class FinalizeResponse(_Out):
    take_result: TakeResult
    #: 이번에 닫은 문제 구간 · 효과 · 장 이벤트
    events: list[CoachEvent] = Field(default_factory=list)
    meta: Meta


# ══════════════════════════════════════════════════════════════════════════
# 공통 1초 판정 결과 (#158 정의의 복사본)
#
# 판정 모듈(시선 · 속도 · 음량 · 군더더기 · 시간)이 같은 모양으로 낸다.
# 코치는 외부 모듈의 결과도 이 모양으로 읽는다 — 그래서 모르는 필드는 무시한다.
# ══════════════════════════════════════════════════════════════════════════


class JudgmentIssue(_In):
    """판정 결과 안의 문제 하나."""

    issue_type: Issue
    area: FeedbackType
    #: 기준값에서 0.5, 아주 나쁨 값에서 1.0
    severity: float = Field(ge=0.0, le=1.0)
    confidence: float = Field(ge=0.0, le=1.0)
    persistence_sec: float = Field(ge=0.0)
    threshold: float
    bad: float
    evidence: dict[str, Any] = Field(default_factory=dict)
    actionable: bool = True


class TallyItem(_In):
    """집계 조각. 구간마다 값을 남겨 두면 소비자가 원하는 구간으로 합친다."""

    t_ms: int
    values: dict[str, float]


class JudgmentResult(_In):
    """한 판정 모듈의 1초 판정 결과."""

    #: gaze · pace · volume · filler · timing
    evaluator: str
    area: FeedbackType
    t_ms: int
    #: 여기까지 집계(tally)에 셌다. 다음 호출의 since_ms 로 쓴다
    counted_until_ms: int
    words_counted_until_ms: int | None = None
    criteria_version: str
    measurable: bool
    state: str
    metrics: dict[str, float | None] = Field(default_factory=dict)
    tally: list[TallyItem] = Field(default_factory=list)
    issues: list[JudgmentIssue] = Field(default_factory=list)
    #: filler 모듈만 쓰는 확장 필드
    words: list[dict[str, Any]] | None = None


class IssueCriteria(_In):
    """문제 하나의 판정 기준. 소비자가 같은 기준으로 다시 셀 수 있게 공개한다."""

    metric: str
    direction: Literal["HIGHER_IS_WORSE", "LOWER_IS_WORSE"]
    threshold: float
    bad: float
    onset_lag_ms: int = 0
    offset_lag_ms: int = 0
