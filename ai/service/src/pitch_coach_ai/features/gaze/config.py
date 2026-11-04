"""시선 판정의 기준값.

- `GazeConfig` — 서버 판정 모듈(`judge` · `summarize` · `criteria`)의 기준값. 서버 판정 기준값은
  이 클래스 한 곳에만 둔다(gaze-tracking.md 4-3). 값을 바꾸면 `criteria_version` 의 해시가 바뀐다.
  모두 초기값이고 실측으로 고른 값이 아니다(gaze-tracking.md 13절).

1초 기록을 만드는 값(1초 길이 · 프레임 수 · 득표율)은 브라우저 엔진의 값이라 여기 없다
(configs/evidence.yaml → gaze_lab.config.EvidenceConfig → web 의 defaults.generated.ts).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, model_validator

#: BE 가 /coach/evaluate 에 싣는 시선 · 음량 1초 기록의 길이(architecture.md 2절).
#: 판정 창은 이 안에서 정한다
INPUT_WINDOW_MS = 30_000


class GazeConfig(BaseModel):
    """서버 판정 모듈의 기준값 (gaze-tracking.md 5-4 · 6절)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: 짧은 창: 상태 표시 · 개입 효과(_short 지표) · 측정 비율 · 평균 신뢰도
    short_window_ms: int = 5_000
    #: 최근 창: 응시 비율 · 대본 의존 비율 · 눈맞춤 부족
    recent_window_ms: int = 30_000

    #: 이어짐 문제: 기준 시간 이상 이어지면 문제(severity 0.5), 아주 나쁨 시간에서 severity 1.0
    script_run_ms: int = 3_000
    script_run_bad_ms: int = 10_000
    screen_run_ms: int = 5_000
    screen_run_bad_ms: int = 15_000
    away_run_ms: int = 2_000
    away_run_bad_ms: int = 8_000
    #: GAZE_ON_SCRIPT 는 짧은 창의 대본 응시 비율도 이 값 이상이어야 한다(우연히 생긴 연속을 거른다)
    script_short_ratio: float = 0.6
    #: 짧은 창의 대본 응시 비율이 이 값 이상이면 상태를 SCRIPT 로 둔다
    state_script_ratio: float = 0.5

    #: 눈맞춤 부족: 최근 창의 측정 시간이 충분할 때 청중 응시 비율이 기준 미만
    low_eye_contact_ratio: float = 0.30
    low_eye_contact_bad: float = 0.0
    low_eye_contact_min_measured_ms: int = 15_000

    #: 측정 불가: 측정 비율(짧은 창 · 최근 창 중 나쁜 쪽) 또는 평균 신뢰도(짧은 창)가 기준 미만
    min_measured_ratio: float = 0.5
    min_mean_reliability: float = 0.5
    unmeasurable_bad: float = 0.0

    #: Take 시작 뒤 이 시간 동안은 GAZE_UNMEASURABLE 말고는 내지 않는다(표본이 적어 비율이 튄다)
    take_start_quiet_ms: int = 5_000

    #: 말한 1초: 같은 1초의 voiced_ms 가 이 값 이상. 소리 크기 config.py 와 같은 값이어야 한다
    #: (architecture.md 6-2, 계약 테스트)
    speaking_min_voiced_ms: int = 300
    #: 말한 1초 동안 대본 응시가 이 시간 이상 이어졌다가 끝나면 reading_runs 1개
    reading_run_min_ms: int = 3_000

    @model_validator(mode="after")
    def _check(self) -> GazeConfig:
        if not 0 < self.short_window_ms <= self.recent_window_ms <= INPUT_WINDOW_MS:
            raise ValueError("0 < short_window_ms <= recent_window_ms <= 30000 이어야 한다")
        for name in ("script", "screen", "away"):
            lo, hi = getattr(self, f"{name}_run_ms"), getattr(self, f"{name}_run_bad_ms")
            if not 0 < lo < hi:
                raise ValueError(f"0 < {name}_run_ms < {name}_run_bad_ms 이어야 한다")
        if not 0 <= self.low_eye_contact_bad < self.low_eye_contact_ratio <= 1:
            raise ValueError("0 <= low_eye_contact_bad < low_eye_contact_ratio <= 1 이어야 한다")
        if not 0 <= self.unmeasurable_bad < self.min_measured_ratio <= 1:
            raise ValueError("0 <= unmeasurable_bad < min_measured_ratio <= 1 이어야 한다")
        if not 0 <= self.unmeasurable_bad < self.min_mean_reliability <= 1:
            raise ValueError("0 <= unmeasurable_bad < min_mean_reliability <= 1 이어야 한다")
        for name in ("script_short_ratio", "state_script_ratio"):
            if not 0 < getattr(self, name) <= 1:
                raise ValueError(f"0 < {name} <= 1 이어야 한다")
        for name in (
            "low_eye_contact_min_measured_ms",
            "take_start_quiet_ms",
            "speaking_min_voiced_ms",
            "reading_run_min_ms",
        ):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} 는 0 이상이어야 한다")
        return self


DEFAULT = GazeConfig()
