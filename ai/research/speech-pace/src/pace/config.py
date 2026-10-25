from pydantic import BaseModel, ConfigDict


class PaceConfig(BaseModel):
    """judge · summarize · criteria 의 기준값. 해시가 criteria_version 에 들어간다.

    모두 잠정값이다(pace.md 13절). 빠름 · 느림 기준은 노트북 값,
    나머지는 코치 연구용 대역(pace-0.2) 값.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    #: 최근 창 (상태 · 문제). cpm 은 창 안 발화 시간 · 단어 수가 이 이상일 때만 낸다
    window_ms: int = 15_000
    min_speak_ms: int = 4_000
    min_words: int = 5
    #: 짧은 창 (개입 효과, cpm_short)
    short_window_ms: int = 6_000
    short_min_speak_ms: int = 2_500
    short_min_words: int = 3
    #: 빠름 기준 · 아주 빠름 값 (높을수록 나쁘다)
    fast_cpm: float = 350.0
    fast_cpm_bad: float = 450.0
    #: 느림 기준 · 아주 느림 값 (낮을수록 나쁘다)
    slow_cpm: float = 275.0
    slow_cpm_bad: float = 200.0
    #: 두 확정 단어 사이 간격이 이보다 길면 멈춤 하나
    pause_min_ms: int = 2_000
    #: confidence = base + (1 − base) × min(1, 최근 창 발화 시간 ÷ full_speak_ms)
    confidence_base: float = 0.6
    confidence_full_speak_ms: int = 8_000
    #: 최근 창 때문에 빠름 · 느림의 시작 · 끝이 늦게 잡히는 폭
    onset_lag_ms: int = 7_500
    offset_lag_ms: int = 7_500


DEFAULT = PaceConfig()
