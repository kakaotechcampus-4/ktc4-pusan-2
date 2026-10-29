"""연구용 판정 대역 패키지 — 옛 코치 평가기의 규칙 · 기준값을 #152~#155 계약 모양으로 옮긴 것.

기능 모듈이 나오면 이 대역 대신 그 모듈을 쓴다. 코치 코어(`coach`)는 이 패키지를 모른다.

- `real_judges()`: 서비스의 실제 판정 모듈 넷(시선 · 군더더기 · 말 속도 · 소리 크기) 묶음.
  재생 · 실험이 쓴다
  (코치 기능 문서 11절 '옮긴 뒤 같은 결과인지 확인' 3)
- `lab_judges()`: 네 대역 묶음. 대역 자체의 테스트가 쓴다
"""

from __future__ import annotations

from pitch_coach_ai.features.coach.judges import Judges
from pitch_coach_ai.features.filler import core as real_filler
from pitch_coach_ai.features.gaze import core as real_gaze
from pitch_coach_ai.features.pace import core as real_pace
from pitch_coach_ai.features.volume import core as real_volume

from . import filler, gaze, pace, volume

__all__ = ["filler", "gaze", "lab_judges", "pace", "real_judges", "volume"]


def lab_judges() -> Judges:
    """네 대역 모듈을 코치가 받는 묶음으로 만든다. 기준 음량은 volume 의 말한 1초 고르기
    (speaking_levels)와 baseline 으로 잡는다."""
    return Judges(
        gaze=gaze,
        pace=pace,
        volume=volume,
        filler=filler,
        speaking_levels=volume.speaking_levels,
        baseline=volume.baseline,
    )


def real_judges() -> Judges:
    """서비스의 실제 판정 모듈 넷. 기준 음량도 서비스 소리 크기 모듈로 잡는다."""
    return Judges(
        gaze=real_gaze,
        pace=real_pace,
        volume=real_volume,
        filler=real_filler,
        speaking_levels=real_volume.speaking_levels,
        baseline=real_volume.baseline,
    )
