"""소리 크기 · 침묵 판정의 입출력 계약 (pydantic).

입력은 음량 1초 기록(``VoiceRecord``)이다. FE 가 만들어 BE → 코치를 거쳐 오고(architecture.md 6-2),
코치가 ``VolumeInput`` 으로 묶어 ``judge`` 에 넘긴다. 입력 모델은 모르는 필드를 무시한다.

출력은 ``judge`` 의 1초 판정 결과 두 개(``VOLUME`` · ``PAUSE``, 이 순서), ``summarize`` 의 영역별
지표, ``criteria`` 의 판정 기준이다. 공통 필드와 커서 규칙은 architecture.md 7절을 따른다.
출력 모델은 정해진 필드만 낸다. 모듈끼리 import 하지 않으므로 공통 결과 형식도 여기에 둔다.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Area = Literal["VOLUME", "PAUSE"]
VolumeState = Literal["LOW", "NORMAL", "UNKNOWN"]
PauseState = Literal["SILENT", "NORMAL", "UNKNOWN"]
IssueType = Literal["VOLUME_LOW", "LONG_SILENCE"]
Direction = Literal["HIGHER_IS_WORSE", "LOWER_IS_WORSE"]

Ratio = float  # 0~1


class _In(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)


class _Out(BaseModel):
    # 출력 계약은 키 집합이 고정이다. 코어가 키를 더하거나 빠뜨리면 계약 테스트가 깨진다
    model_config = ConfigDict(extra="forbid")


# --------------------------------------------------------------------------
# 입력
# --------------------------------------------------------------------------


class VoiceRecord(_In):
    """음량 1초 기록 한 개 (5-2)."""

    t_ms: int = Field(ge=0, description="Take 시작 기준 이 1초의 시작 시각. 시선 1초 기록과 같은 격자")
    duration_ms: int = Field(gt=0, description="보통 1000. stop 직전 마지막 조각만 짧다")
    level_db: float | None = Field(
        default=None, description="말한 구간의 Leq(A 가중 · Fast, dBFS). 말한 구간이 없으면 null"
    )
    voiced_ms: int = Field(ge=0, description="이 1초 중 '말하는 중'이었던 ms")
    silence_ms: int = Field(
        ge=0, description="이 1초 끝에서 거꾸로 센 연속 무음 길이. 앞 1초들에서 이어지면 1초를 넘는다"
    )
    audio_live: bool = Field(description="이 1초 동안 오디오가 흘렀나")


class VolumeInput(_In):
    """``judge(inputs, t_ms, config)`` 의 ``inputs`` (코치가 만든다)."""

    records: list[VoiceRecord] = Field(
        default_factory=list, description="최근 30초 음량 1초 기록. BE 가 매 요청 겹쳐 보낸다"
    )
    base_level_db: float | None = Field(
        default=None, description="기준 음량. null 이면 VOLUME 은 잴 수 없다"
    )
    since_ms: int = Field(ge=0, description="시각 커서. t_ms 가 이 값 이상인 1초가 새것이다")


# --------------------------------------------------------------------------
# 출력: judge
# --------------------------------------------------------------------------


class VolumeMetrics(_Out):
    #: 짧은 창의 말한 1초들의 Leq. 말한 1초가 없으면 null
    level_db: float | None
    #: level_db − base_level_db. 기준 음량이 없으면 null
    voice_diff_db: float | None


class PauseMetrics(_Out):
    #: 마지막 1초 끝의 연속 무음을 오디오가 다시 흐른 뒤로 자른 값. 오디오가 멈췄으면 null
    silence_ms: int | None


class VolumeTally(_Out):
    """VOLUME 의 1초 몫. 값이 0인 키는 뺄 수 있다."""

    voiced_ms: float = 0
    #: 10^(level_db/10) × voiced_ms
    voiced_energy: float = 0
    #: 그 1초에서 끝나는 짧은 창의 voice_diff_db 가 작음 기준 미만이면 그 1초의 voiced_ms
    low_ms: float = 0
    #: 기준 음량이 있을 때 그 1초의 voiced_ms
    level_voiced_ms: float = 0
    #: base_level_db × level_voiced_ms
    base_db_ms: float = 0
    audio_live_ms: float = 0
    total_ms: float = 0


class PauseTally(_Out):
    """PAUSE 의 1초 몫. 값이 0인 키는 뺄 수 있다."""

    #: 그 1초 안에서 잘린 silence_ms 가 긴 침묵 기준을 처음 넘으면 1
    long_silence_count: float = 0
    audio_live_ms: float = 0
    total_ms: float = 0


class VolumeTallyItem(_Out):
    t_ms: int
    values: VolumeTally


class PauseTallyItem(_Out):
    t_ms: int
    values: PauseTally


class _Issue(_Out):
    #: 기준값에서 0.5, 아주 나쁨 값에서 1.0
    severity: Ratio = Field(ge=0, le=1)
    #: 잴 수 있으면 1
    confidence: Ratio = Field(ge=0, le=1)
    persistence_sec: float = Field(ge=0)
    threshold: float
    bad: float
    evidence: dict[str, Any] = Field(default_factory=dict)
    actionable: bool = True


class VolumeLowIssue(_Issue):
    issue_type: Literal["VOLUME_LOW"]
    area: Literal["VOLUME"]


class LongSilenceIssue(_Issue):
    """persistence_sec 는 silence_ms ÷ 1000."""

    issue_type: Literal["LONG_SILENCE"]
    area: Literal["PAUSE"]


class _Judgment(_Out):
    evaluator: Literal["volume"]
    t_ms: int
    #: max(t_ms, since_ms). 코치가 다음 요청의 since_ms 로 넘긴다
    counted_until_ms: int
    #: volume-<기능 버전>+<config 해시 12자리>
    criteria_version: str
    measurable: bool


class VolumeJudgment(_Judgment):
    """``judge`` 결과의 첫째 — 소리 크기."""

    area: Literal["VOLUME"]
    state: VolumeState
    metrics: VolumeMetrics
    tally: list[VolumeTallyItem]
    issues: list[VolumeLowIssue]


class PauseJudgment(_Judgment):
    """``judge`` 결과의 둘째 — 침묵."""

    area: Literal["PAUSE"]
    state: PauseState
    metrics: PauseMetrics
    tally: list[PauseTallyItem]
    issues: list[LongSilenceIssue]


# --------------------------------------------------------------------------
# 출력: summarize · criteria
# --------------------------------------------------------------------------


class VolumeSummary(_Out):
    """분모가 0이면 null. measured_ratio 만 분모가 0이면 0."""

    #: 10·log10(voiced_energy ÷ voiced_ms)
    level_db: float | None
    #: level_db − base_db_ms ÷ level_voiced_ms
    voice_diff_db: float | None
    #: low_ms ÷ voiced_ms
    low_ratio: Ratio | None
    #: min(audio_live_ms ÷ total_ms, level_voiced_ms ÷ voiced_ms). voiced_ms 가 0이면 0
    measured_ratio: Ratio


class PauseSummary(_Out):
    long_silence_count: int
    #: audio_live_ms ÷ total_ms
    measured_ratio: Ratio


class Summary(_Out):
    """``summarize`` → 두 영역."""

    VOLUME: VolumeSummary
    PAUSE: PauseSummary


class IssueCriteria(_Out):
    """문제 하나의 판정 기준 (architecture.md 7절)."""

    metric: str
    direction: Direction
    threshold: float
    bad: float
    onset_lag_ms: int = 0
    offset_lag_ms: int = 0


class Criteria(_Out):
    """``criteria`` → 문제마다 기준."""

    VOLUME_LOW: IssueCriteria
    LONG_SILENCE: IssueCriteria
