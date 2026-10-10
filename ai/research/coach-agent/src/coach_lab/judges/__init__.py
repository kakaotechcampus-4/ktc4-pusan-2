"""연구용 판정 대역 패키지 — 지금 코치 평가기의 규칙 · 기준값을 #152~#155 계약 모양으로 옮긴 것.

기능 모듈이 나오면 이 대역 대신 그 모듈을 쓴다. 코치 코어(`coach`)는 이 패키지를 모른다.
"""

from __future__ import annotations

from coach.judges import Judges

from . import filler, gaze, pace, volume

__all__ = ["filler", "gaze", "lab_judges", "pace", "volume"]


def lab_judges() -> Judges:
    """네 대역 모듈을 코치가 받는 묶음으로 만든다. 기준 음량은 volume.baseline 으로 잡는다."""
    return Judges(gaze=gaze, pace=pace, volume=volume, filler=filler, baseline=volume.baseline)
