"""군더더기 판정의 입출력 계약 (pydantic).

기준: docs/ai/features/filler.md 5절. 공통 필드(1초 판정 결과 · 커서 · STT 모듈 입력)는
docs/ai/architecture.md 7절을 따른다. 시각은 모두 Take 시작 기준 ms 다.

입력 모델은 모르는 필드를 무시하고, 출력 모델은 정해진 필드만 낸다(filler.md 4-3).
다른 모듈을 import 하지 않으므로 architecture.md 7절의 공통 결과 모양
(`JudgmentIssue` · `JudgmentResult` · `TallyItem`)도 여기에 옮겨 둔다.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

SttStatus = Literal["connecting", "ok", "reconnecting", "degraded", "closed"]
Tier = Literal["T1", "T2", "T3"]
State = Literal["NORMAL", "HIGH", "UNKNOWN"]


class _Input(BaseModel):
    model_config = ConfigDict(extra="ignore")


class _Output(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --------------------------------------------------------------------------
# 입력: judge(inputs, t_ms, config) 의 inputs (filler.md 5-2)
# --------------------------------------------------------------------------


class Word(_Input):
    """STT 확정 단어 하나. `word` 는 문장부호 없는 어절이다."""

    word: str
    start_ms: int
    end_ms: int


class FillerInput(_Input):
    #: 판정 시각. judge 의 t_ms 인자와 같아야 한다
    t_ms: int
    #: 시각 커서. 시간 합계를 이 시각부터 센다
    since_ms: int
    #: 단어 커서. end_ms 가 이 값보다 큰 단어가 새것이다
    words_since_ms: int
    #: 최근 60초 STT 확정 단어. BE 가 매 요청 겹쳐 보낸다
    words: list[Word] = Field(default_factory=list)
    #: 창 안의 문장 끝 신호 시각 (Deepgram UtteranceEnd 가 알린 마지막 단어의 끝 시각)
    utterance_ends: list[int] = Field(default_factory=list)
    #: finalize 마지막 창의 Take 끝 시각. evaluate 에서는 None
    take_end_ms: int | None = None
    #: ok 가 아니면 잴 수 없음. 빠지면 None(모름)으로 받아 ok 가 아닌 것으로 본다
    stt_status: SttStatus | None = None
    #: STT 가 마지막으로 ok 가 된 시각. 끊긴 적이 없으면 0
    stt_ok_since_ms: int = 0


# --------------------------------------------------------------------------
# 출력: judge → JudgmentResult 하나를 담은 목록 (filler.md 5-3)
# --------------------------------------------------------------------------


class WordJudgment(_Output):
    """창 안 단어 하나의 판정."""

    start_ms: int
    end_ms: int
    #: 입력 원문 그대로 (문장부호를 떼기 전)
    word: str
    #: 사전에 없으면 None
    tier: Tier | None
    #: None 이면 보류(뒤 간격을 아직 알 수 없음)
    is_filler: bool | None
    #: 사람이 읽는 판정 이유
    reason: str


class FillerMetrics(_Output):
    #: 최근 창의 군더더기 수. 잴 수 없으면 None
    recent_filler_count: int | None
    #: 최근 창의 분당 군더더기 수. 관측 시간이 짧거나 잴 수 없으면 None
    filler_per_min: float | None


class TallyItem(_Output):
    """새로 센 몫 하나. 값이 0 인 키는 뺀다.

    - 단어 몫: t_ms = 그 단어의 start_ms,
      values ⊂ {filler_count, filler_t1 · filler_t2 · filler_t3, "filler_word:<단어>"}
    - 1초 몫: t_ms = 그 1초의 시작, values ⊂ {elapsed_ms, total_ms}
    """

    t_ms: int
    values: dict[str, int]


class JudgmentIssue(_Output):
    issue_type: Literal["FILLER_FREQUENT"]
    area: Literal["FILLER"] = "FILLER"
    #: 기준값에서 0.5, 아주 나쁨 값에서 1.0, 그 사이 직선, 1.0 에서 자른다
    severity: float = Field(ge=0.0, le=1.0)
    #: 잴 수 있으면 1
    confidence: float = Field(ge=0.0, le=1.0)
    persistence_sec: float = Field(ge=0.0)
    threshold: float
    bad: float
    #: recent_filler_count 와 filler_per_min
    evidence: dict[str, float | int | None] = Field(default_factory=dict)
    actionable: bool = True


class JudgmentResult(_Output):
    evaluator: Literal["filler"] = "filler"
    area: Literal["FILLER"] = "FILLER"
    t_ms: int
    #: 이번까지 센 시각 커서
    counted_until_ms: int
    #: 이번까지 센 단어 커서. 보류 단어가 있으면 그 앞에서 멈춘다
    words_counted_until_ms: int
    #: filler-<버전>+<config 해시 12자리>
    criteria_version: str
    #: STT 가 ok 면 true
    measurable: bool
    #: 잴 수 없으면 UNKNOWN
    state: State
    #: 창 안 단어별 판정 (이 모듈만)
    words: list[WordJudgment]
    metrics: FillerMetrics
    tally: list[TallyItem]
    #: 잴 수 없으면 빈 목록
    issues: list[JudgmentIssue]


# --------------------------------------------------------------------------
# 출력: summarize(tally, config) → {"FILLER": {...}} (filler.md 5-3)
# --------------------------------------------------------------------------

#: summarize 입력: 장 · Take 동안의 tally 합계 (영역 → 이름 → 합)
Tally = dict[str, dict[str, float]]


class FillerSummary(_Output):
    filler_count: int
    #: filler_count ÷ elapsed_ms × 60000. 분모가 0 이면 None
    filler_per_min: float | None
    #: {"T1", "T2", "T3"} 별 횟수
    by_tier: dict[str, int]
    #: 센 단어별 횟수. 많은 순, 같으면 단어 순
    by_word: dict[str, int]
    #: elapsed_ms ÷ total_ms. 분모가 0 이면 0
    measured_ratio: float


class Summary(_Output):
    FILLER: FillerSummary


# --------------------------------------------------------------------------
# 출력: criteria(config) (filler.md 5-3)
# --------------------------------------------------------------------------


class IssueCriteria(_Output):
    metric: Literal["recent_filler_count"]
    direction: Literal["HIGHER_IS_WORSE"]
    threshold: float
    bad: float
    #: 최근 창 때문에 문제의 시작 · 끝이 늦게 잡히는 폭
    onset_lag_ms: int
    offset_lag_ms: int
