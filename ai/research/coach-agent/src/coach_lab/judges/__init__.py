"""연구용 판정 대역 — 지금 코치 평가기의 규칙 · 기준값을 #152~#155 계약 모양으로 옮긴 것.

기능 모듈이 나오면 이 대역 대신 그 모듈을 쓴다. 코치 코어(`coach`)는 이 패키지를 모른다.
"""

from __future__ import annotations

from types import ModuleType

from . import gaze

__all__ = ["gaze", "lab_judges"]


def lab_judges() -> dict[str, ModuleType]:
    """영역 이름 → 판정 모듈. judge · summarize · criteria 를 갖는다(volume 은 baseline 도)."""
    return {"gaze": gaze}
