"""1단계: 판단 가능 여부. 측정이 부족한 차원은 문제로도 강점으로도 말하지 않는다."""

from __future__ import annotations

from typing import Any

from .config import ReviewConfig
from .vocab import DIMENSIONS


def check_evaluable(
    take_result: dict[str, Any], script_coverage: dict[str, Any] | None, cfg: ReviewConfig
) -> dict[str, bool]:
    """차원마다 판단 가능한지.

    - 차원에 속한 영역 중 하나라도 measured_ratio < min_measured_ratio (또는 없음) → False
    - content_delivery: 대본 전달도가 없거나 scored_ratio < min_scored_ratio → False
      (일부 장만 채점된 점수로 강점이라 하지 않게)
    """
    c = cfg.evaluable
    evaluable: dict[str, bool] = {}
    for dim, spec in DIMENSIONS.items():
        if dim == "content_delivery":
            if script_coverage is None:
                evaluable[dim] = False
                continue
            scored_ratio = (script_coverage.get("take_scores") or {}).get("scored_ratio")
            evaluable[dim] = scored_ratio is not None and scored_ratio >= c.min_scored_ratio
            continue

        ok = True
        for area in spec.areas:
            ratio = (take_result["areas"].get(area) or {}).get("measured_ratio")
            if ratio is None or ratio < c.min_measured_ratio:
                ok = False
                break
        evaluable[dim] = ok
    return evaluable
