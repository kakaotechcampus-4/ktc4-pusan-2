"""말 속도 판정의 입출력 계약 (pydantic).

기준: docs/ai/features/pace.md 5절. 공통 필드(1초 판정 결과 · 커서 · STT 모듈 입력)는
docs/ai/architecture.md 7절을 따른다. 시각은 모두 Take 시작 기준 ms 다.

입력 모델은 모르는 필드를 무시하고(``utterance_ends`` · ``take_end_ms`` 가 실려 와도 쓰지 않는다),
출력 모델은 정해진 필드만 낸다. 다른 모듈을 import 하지 않으므로 공통 결과 모델도 여기에 둔다.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

SttStatus = Literal["connecting", "ok", "reconnecting", "degraded", "closed"]
State = Literal["SLOW", "NORMAL", "FAST", "UNKNOWN"]
IssueType = Literal["PACE_FAST", "PACE_SLOW"]
Direction = Literal["HIGHER_IS_WORSE", "LOWER_IS_WORSE"]


class _Input(BaseModel):
    model_config = ConfigDict(extra="ignore")


class _Output(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --------------------------------------------------------------------------
# 입력: judge(inputs, t_ms, config) 의 inputs (5-2)
# --------------------------------------------------------------------------


class Word(_Input):
    """STT 확정 단어 하나."""

    word: str
    start_ms: int
    end_ms: int


class FillerMark(_Input):
    """같은 요청의 군더더기 판정 중 군더더기 · 보류 단어 하나.

    말 속도 단어와는 (start_ms, end_ms) 로 맞춘다.
    """

    start_ms: int
    end_ms: int
    #: True 면 군더더기, None(키가 없어도)이면 보류
    is_filler: bool | None = None


class PaceInput(_Input):
    #: 시각 커서. 시간 합계를 이 시각부터 센다
    since_ms: int
    #: 단어 커서. end_ms 가 이 값보다 큰 단어가 새것이다
    words_since_ms: int
    #: 최근 60초 STT 확정 단어. BE 가 매 요청 겹쳐 보낸다
    words: list[Word] = Field(default_factory=list)
    #: ok 가 아니면 잴 수 없음. 빠지면 None(모름)으로 받아 ok 가 아닌 것으로 본다
    stt_status: SttStatus | None = None
    #: STT 가 마지막으로 ok 가 된 시각. 끊긴 적이 없으면 0
    stt_ok_since_ms: int = 0
    #: [] = 군더더기 없음, None = 군더더기 판정 실패. 필수라 기본값이 없다
    fillers: list[FillerMark] | None


# --------------------------------------------------------------------------
# 출력: judge → JudgmentResult 하나를 담은 목록 (5-3)
# --------------------------------------------------------------------------


class PaceMetrics(_Output):
    #: 최근 창 CPM. 최근 창의 최소 발화 시간 · 단어 수를 채울 때만 값이 있다
    cpm: float | None
    #: 짧은 창 CPM (개입 효과용). 짧은 창 조건을 채울 때만 값이 있다
    cpm_short: float | None
    #: 최근 창 안 단어 발화 시간 합
    speak_ms: int | None


class TallyItem(_Output):
    """새로 센 몫 하나. 값이 0 인 키는 뺀다.

    - 단어 몫: t_ms = 그 단어의 start_ms, values ⊂ {chars, speak_ms, pause_count, pause_ms}
    - 1초 몫: t_ms = 그 1초의 시작, values ⊂ {ok_ms, total_ms}
    """

    t_ms: int
    values: dict[str, int]


class JudgmentIssue(_Output):
    issue_type: IssueType
    area: Literal["SPEED"] = "SPEED"
    #: 기준값에서 0.5, 아주 나쁨 값에서 1.0, 그 사이 직선, 1.0 에서 자른다
    severity: float = Field(ge=0.0, le=1.0)
    #: 최근 창 안 발화 시간이 길수록 크다 (식은 config.py)
    confidence: float = Field(ge=0.0, le=1.0)
    persistence_sec: float = Field(ge=0.0)
    threshold: float
    bad: float
    #: cpm 과 창 길이
    evidence: dict[str, float | int | None] = Field(default_factory=dict)
    actionable: bool = True


class JudgmentResult(_Output):
    evaluator: Literal["pace"] = "pace"
    area: Literal["SPEED"] = "SPEED"
    t_ms: int
    #: 이번까지 센 시각 커서
    counted_until_ms: int
    #: 이번까지 센 단어 커서. 보류 단어가 있으면 그 앞에서 멈춘다
    words_counted_until_ms: int
    #: pace-<버전>+<config 해시 12자리>
    criteria_version: str
    #: STT 가 ok, fillers 가 null 이 아님, 최근 창 발화 시간 · 단어 수가 기준 이상
    measurable: bool
    #: 잴 수 없으면 UNKNOWN
    state: State
    metrics: PaceMetrics
    tally: list[TallyItem] = Field(default_factory=list)
    #: 잴 수 없으면 빈 목록
    issues: list[JudgmentIssue] = Field(default_factory=list)


# --------------------------------------------------------------------------
# 출력: summarize(tally, config) → {"SPEED": {...}} (5-3)
# --------------------------------------------------------------------------

#: summarize 입력: 장 · Take 동안의 tally 합계 (영역 → 이름 → 합)
Tally = dict[str, dict[str, float]]


class SpeedSummary(_Output):
    #: chars ÷ speak_ms × 60000. 분모가 0 이면 null
    cpm: float | None
    #: 합
    pause_count: int
    #: ok_ms ÷ total_ms. 분모가 0 이면 0
    measured_ratio: float


class PaceSummary(_Output):
    SPEED: SpeedSummary


# --------------------------------------------------------------------------
# 출력: criteria(config) (5-3)
# --------------------------------------------------------------------------


class IssueCriteria(_Output):
    metric: Literal["cpm"] = "cpm"
    direction: Direction
    threshold: float
    bad: float
    #: 최근 창 때문에 빠름 · 느림이 늦게 잡히는 폭
    onset_lag_ms: int
    offset_lag_ms: int
