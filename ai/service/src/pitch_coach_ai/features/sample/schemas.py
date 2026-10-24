"""대역의 입력 · 출력 모델 = 계약 (`docs/ai/architecture.md` 7절).

- 입력 `SampleInput`: 시각 커서 `since_ms` 와 1초 기록 목록 `records`.
  판정 시각 `t_ms` 는 `judge` 의 인자로 따로 받는다(7절 함수 모양)
- 출력 `JudgmentResult`: 공통 1초 판정 결과(#158). 실제 판정 모듈과 같은 필드이고,
  STT 모듈(군더더기 · 말 속도)만 쓰는 `words_counted_until_ms` · `words` 는 뺐다
- 입력 모델은 모르는 필드를 무시하고, 출력 모델은 정해진 필드만 낸다 (2 · 8절).
  그래서 1초 기록에 시선 `state` 같은 필드가 더 와도 그대로 받는다
"""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

#: 영역 (`architecture.md` 6-3). 한 스키마 버전 안에서 추가만 한다
Area = Literal["GAZE", "SPEED", "VOLUME", "PAUSE", "FILLER", "CONTENT", "TIME"]


class _In(BaseModel):
    model_config = ConfigDict(extra="ignore")


class _Out(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SampleRecord(_In):
    """1초 기록 하나. `t_ms` · `duration_ms` 만 읽는다."""

    t_ms: int = Field(ge=0)
    duration_ms: int = Field(1000, gt=0)


class SampleInput(_In):
    #: 이 시각 이상에서 시작한 1초가 새것이다. 지난 결과의 `counted_until_ms` 를 넘긴다
    since_ms: int = Field(ge=0)
    records: list[SampleRecord]


class TallyItem(_Out):
    """새로 센 몫 한 조각. `values` 에는 더할 수 있는 값만 넣는다."""

    t_ms: int
    values: dict[str, float]


class JudgmentIssue(_Out):
    """지금 걸린 문제 하나. 대역은 문제를 내지 않지만 결과 모양을 맞추려고 둔다."""

    issue_type: str
    area: Area
    #: 기준값에서 0.5, 아주 나쁨 값에서 1.0
    severity: float = Field(ge=0, le=1)
    confidence: float = Field(ge=0, le=1)
    persistence_sec: float = Field(ge=0)
    threshold: float
    bad: float
    evidence: dict[str, Any] = Field(default_factory=dict)
    actionable: bool = True


class JudgmentResult(_Out):
    """한 판정 모듈의 1초 판정 결과. 결과 하나가 영역 하나다."""

    evaluator: str
    area: Area
    t_ms: int
    #: 이번까지 센 위치. 코치가 다음 요청의 `since_ms` 로 넘긴다
    counted_until_ms: int
    criteria_version: str
    measurable: bool
    state: str
    metrics: dict[str, float | None]
    tally: list[TallyItem]
    issues: list[JudgmentIssue]


class IssueCriteria(_Out):
    """문제 하나의 판정 기준 (`criteria()` 의 값)."""

    metric: str
    direction: Literal["HIGHER_IS_WORSE", "LOWER_IS_WORSE"]
    threshold: float
    bad: float
    onset_lag_ms: int
    offset_lag_ms: int
