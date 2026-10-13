"""1단계: 판단 가능 여부. 측정이 부족한 차원은 문제로도 강점으로도 말하지 않는다."""

from __future__ import annotations

from typing import Any

from .config import ReviewConfig
from .vocab import DIMENSIONS


def measured_areas(take_result: dict[str, Any]) -> set[str]:
    """잰 영역: Take 결과에 있고 코치가 unmeasured_reason 을 달지 않은 영역.

    측정 비율 기준은 코치가 정하고 리뷰는 따로 두지 않는다 (명세 6-1 1번).
    포함 여부 확인에만 쓴다 (순서를 정하지 않는다).
    """
    return {
        area
        for area, data in take_result["areas"].items()
        if data is not None and data.get("unmeasured_reason") is None
    }


def check_evaluable(
    take_result: dict[str, Any], script_coverage: dict[str, Any] | None, cfg: ReviewConfig
) -> dict[str, bool]:
    """차원마다 판단 가능한지.

    - 차원에 속한 영역이 모두 못 잰 영역(unmeasured_reason 있음 · 없는 영역) → False.
      영역이 여럿인 차원(pacing_rhythm)은 일부만 판단 불가면 잰 영역으로만 본다 (부담도 잰 영역만)
    - content_delivery: 대본 전달도가 없거나 scored_ratio < min_scored_ratio → False
      (일부 장만 채점된 점수로 강점이라 하지 않게).
      take_scores.content_coverage 가 null 이어도 False (채점할 핵심 내용이 없는 대본)
    """
    c = cfg.evaluable
    measured = measured_areas(take_result)
    evaluable: dict[str, bool] = {}
    for dim, spec in DIMENSIONS.items():
        if dim == "content_delivery":
            if script_coverage is None:
                evaluable[dim] = False
                continue
            take_scores = script_coverage.get("take_scores") or {}
            scored_ratio = take_scores.get("scored_ratio")
            evaluable[dim] = (
                scored_ratio is not None
                and scored_ratio >= c.min_scored_ratio
                and take_scores.get("content_coverage") is not None
            )
            continue

        evaluable[dim] = any(area in measured for area in spec.areas)
    return evaluable
