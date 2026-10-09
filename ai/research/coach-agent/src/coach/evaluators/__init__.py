"""평가기 — 측정값을 문제(Detection)로 바꾼다. 말할지는 판단하지 않는다.

순서가 중요합니다: speech 가 cpm 을 채운 뒤 timing 이 그 값으로 SPEED_UP / CONDENSE 를 가른다.
"""

from __future__ import annotations

from ..tick import Detection, Tick
from . import gaze, speech, timing, voice

__all__ = ["Detection", "Tick", "run_all"]


def run_all(tick: Tick) -> None:
    gaze.evaluate(tick)
    voice.evaluate(tick)
    speech.evaluate(tick)
    timing.evaluate(tick)
