"""소리 크기 · 침묵 판정의 기준값.

판정 기준값은 이 파일에만 둔다. 값의 해시가 ``criteria_version`` (``volume-<버전>+<해시 12자리>``)에
들어가서, 값을 바꾸면 결과의 ``criteria_version`` 도 바뀐다.
작음 · 긴 침묵 기준은 옛 평가기(연구용 대역 ``volume-0.2``)의 값이고 실제 데이터로 고르지 않았다.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class Config(BaseModel):
    # 기본값을 함수 인자 기본값으로 함께 쓰므로 바꿀 수 없게 둔다. 모르는 이름은 막는다
    model_config = ConfigDict(frozen=True, extra="forbid")

    #: voice_diff_db 가 이 값 미만이면 작음(LOW · VOLUME_LOW). severity 0.5
    low_diff_db: float = -6.0
    #: 아주 작음. severity 1.0
    low_diff_bad_db: float = -15.0
    #: 짧은 창(평활) 길이. level_db · voice_diff_db 를 이 창의 말한 1초들로 낸다
    smoothing_ms: int = 5_000
    #: 말한 1초의 voiced_ms 기준. 시선 판정과 같은 값이어야 한다(architecture.md 6-2, 계약 테스트).
    #: 시선 값은 서비스 features/gaze/config.py 의 speaking_min_voiced_ms
    voiced_min_ms: int = 300
    #: 짧은 창에 말한 1초가 이만큼 있어야 VOLUME 을 잴 수 있다
    min_voiced_seconds: int = 3
    #: 기준 음량을 Take 첫 발화로 잡을 때 쓰는 말한 1초 표본 수. 앞에서 이만큼의 중앙값
    baseline_samples: int = 15
    #: 연속 무음이 이 값을 넘으면 긴 침묵(SILENT · LONG_SILENCE). severity 0.5
    long_silence_ms: int = 5_000
    #: 아주 긴 침묵. severity 1.0
    long_silence_bad_ms: int = 15_000
    # 판정 창 때문에 문제의 시작 · 끝이 늦게 잡히는 폭.
    # 코치가 Take 결과의 문제 구간을 되돌릴 때 쓴다
    #: VOLUME_LOW 의 시작 · 끝
    volume_lag_ms: int = 2_500
    #: LONG_SILENCE 의 시작. 긴 침묵 기준을 넘어야 잡히므로 그만큼 늦다
    silence_onset_lag_ms: int = 5_000
    #: LONG_SILENCE 의 끝. 말을 다시 시작하면 바로 풀린다
    silence_offset_lag_ms: int = 0
