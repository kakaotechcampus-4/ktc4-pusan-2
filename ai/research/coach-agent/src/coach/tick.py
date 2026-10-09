"""한 번의 판단(Tick)이 들고 다니는 것과, 판정 결과의 문제를 코치 규칙이 읽는 모양(Detection)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .config import CoachConfig
from .schemas import CoachRequest, IssueCriteria, SlidePlan
from .state import CoachState, HistorySample
from .vocab import Issue


@dataclass
class Detection:
    """평가기가 찾은 문제 하나. 판단(말할지)은 하지 않는다 — 측정과 판단의 분리."""

    issue_type: Issue
    #: 기준에서 벗어난 정도. 기준선에서 0.5, '아주 나쁨'에서 1.0
    severity: float
    #: 이 측정을 믿을 수 있는 정도
    confidence: float
    evidence: dict[str, Any]
    #: 센서(시선 · 오디오 · STT)가 쓸 만한가. False 면 후보가 SENSOR_UNUSABLE 로 버려진다
    sensor_ok: bool = True
    slide_number: int | None = None
    #: 미션 · 악화 판단에 쓰는 대표 지표 이름 (tick.metrics 의 키)
    metric: str | None = None
    #: 문구 렌더러가 채울 값
    params: dict[str, Any] = field(default_factory=dict)
    #: 사다리를 최소 이 칸부터 쓴다 (늦었는데 속도로 못 따라잡으면 SPEED_UP 을 건너뛴다)
    min_step: int = 0


@dataclass
class Tick:
    """decide() 한 번이 들고 다니는 맥락. 평가기는 metrics 를 채우고 detections 를 더한다."""

    req: CoachRequest
    cfg: CoachConfig
    state: CoachState
    t: int
    slide_number: int | None
    #: 이 장이 시작된 시각 (Take 시간축)
    slide_start_ms: int | None
    stt_ok: bool
    metrics: dict[str, Any] = field(default_factory=dict)
    detections: list[Detection] = field(default_factory=list)
    filler_new: int = 0
    speaking: bool | None = None
    #: 이번 1초의 기준 대비 음량 (dB). 말하지 않았거나 아직 기준이 없으면 None
    voice_diff_db: float | None = None
    #: 이번 판단이 대표하는 시간. 직전 요청과의 간격 (첫 요청 · 긴 공백은 config 로 제한)
    dt_ms: int = 1_000
    #: 모듈 이름 → issue_type → 판정 기준 (되돌아보기가 기준값을 읽는다)
    criteria: dict[str, dict[str, IssueCriteria]] = field(default_factory=dict)

    def history_since(self, since_ms: int) -> list[HistorySample]:
        return [s for s in self.state.history if s.t_ms > since_ms]

    def sample_at_or_before(self, t_ms: int) -> HistorySample | None:
        best: HistorySample | None = None
        for s in self.state.history:
            if s.t_ms <= t_ms:
                best = s
        return best

    def slide_plan(self, slide_number: int | None) -> SlidePlan | None:
        if slide_number is None:
            return None
        for s in self.req.plan.slides:
            if s.slide_number == slide_number:
                return s
        return None

    def detected(self, issue: Issue) -> bool:
        return any(d.issue_type == issue for d in self.detections)


def ramp(value: float, start: float, bad: float) -> float:
    """start 에서 0.5, bad 에서 1.0 이 되도록 선형으로 올린다. bad < start 면 작을수록 나쁘다."""
    if bad == start:
        return 1.0
    frac = (value - start) / (bad - start)
    return 0.5 + 0.5 * min(1.0, max(0.0, frac))


def nonspace_len(text: str) -> int:
    return len("".join(text.split()))
