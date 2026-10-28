"""3단계: 차원 상태. 순위는 장별이 아니라 차원 합계로 매긴다 (장마다 나누면 1순위가 흔들린다)."""

from __future__ import annotations

from typing import Any

from .burden import Burden
from .config import ReviewConfig
from .vocab import DIMENSION_ORDER, DIMENSIONS, METRIC_TO_DIMENSION


def decide_dimension_states(
    burden: Burden,
    evaluable: dict[str, bool],
    recurring: set[str],
    mission_results: list[dict[str, Any]],
    cfg: ReviewConfig,
) -> dict[str, str]:
    """NOT_EVALUABLE → PRIORITY → IMPROVED → STRENGTH → STABLE 순으로 처음 맞는 상태.

    - PRIORITY: 부담 ≥ priority_burden_min 중 부담 큰 순(같으면 차원 표 순서) priority_max_count 개
    - IMPROVED: 직전 반복 문제였는데 이번 부담이 priority_burden_min 미만, 또는 미션을 이루고 부담 0
      (부담은 기준 이상인데 개수 상한에서 밀린 반복 문제는 STABLE — 여전히 문제다)
    - STRENGTH: 부담 0
    - STABLE: 부담은 있지만 PRIORITY 는 아님
    """
    c = cfg.dimension
    candidates = [
        dim
        for dim in DIMENSIONS
        if evaluable[dim] and burden[dim]["total"] >= c.priority_burden_min
    ]
    candidates.sort(key=lambda d: (-burden[d]["total"], DIMENSION_ORDER[d]))
    priority = set(candidates[: c.priority_max_count])

    achieved = {
        METRIC_TO_DIMENSION.get(m["metric"]) for m in mission_results if m["status"] == "ACHIEVED"
    } - {None}

    states: dict[str, str] = {}
    for dim in DIMENSIONS:
        total = burden[dim]["total"]
        no_problem = total == 0
        if not evaluable[dim]:
            states[dim] = "NOT_EVALUABLE"
        elif dim in priority:
            states[dim] = "PRIORITY"
        elif (dim in recurring and total < c.priority_burden_min) or (
            dim in achieved and no_problem
        ):
            states[dim] = "IMPROVED"
        elif no_problem:
            states[dim] = "STRENGTH"
        else:
            states[dim] = "STABLE"
    return states
