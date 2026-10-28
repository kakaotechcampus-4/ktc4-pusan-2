"""6단계: 이전 Take 비교. 바로 앞 Take(previous_takes[0])와 같은 지표의 이번 값 · 이전 값.

비교하는 지표와 좋은 방향은 config.py 의 comparison.direction 표다.
값은 잰 영역에서만 읽는다
(코치가 unmeasured_reason 을 단 영역 · 장 metrics 가 null 인 장은 값이 없다).
대본 전달도는 Take 값만 비교하고, scored_ratio 가 기준 미만인 Take 의 값은 쓰지 않는다
(일부 장만 채점된 점수로 나아졌다고 하지 않게). 한쪽 값이 없으면 비교하지 않는다.
"""

from __future__ import annotations

import math
from typing import Any

from .config import ReviewConfig
from .evaluable import measured_areas
from .metrics import slide_target_ms, speed_threshold
from .vocab import DIMENSIONS

#: (지표, 장 번호). Take 값은 장 번호가 None
Key = tuple[str, int | None]


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _values(
    take_result: dict[str, Any], take_scores: dict[str, Any] | None, cfg: ReviewConfig
) -> dict[Key, float]:
    """Take 하나의 비교할 수 있는 값 {(지표, 장): 값}"""
    compared = cfg.comparison.direction
    values: dict[Key, float] = {}
    measured = measured_areas(take_result)
    for area, data in take_result["areas"].items():
        if area not in measured:
            continue
        for metric, value in (data.get("take") or {}).items():
            values[(metric, None)] = value
        for s in data.get("slides") or []:
            for metric, value in (s.get("metrics") or {}).items():
                values[(metric, s["slide_number"])] = value

    scores = take_scores or {}
    scored_ratio = scores.get("scored_ratio")
    if _is_number(scored_ratio) and scored_ratio >= cfg.evaluable.min_scored_ratio:
        for metric in DIMENSIONS["content_delivery"].metrics:
            values[(metric, None)] = scores.get(metric)

    return {k: v for k, v in values.items() if k[0] in compared and _is_number(v)}


def _bounds(
    metric: str, slide_number: int | None, take_result: dict[str, Any]
) -> tuple[float, float] | None:
    """범위가 좋은 지표의 그 Take 기준 (아래, 위).

    한쪽이 없으면 그쪽은 열려 있다. 기준이 없으면 None
    """
    low = high = None
    if metric == "cpm":
        low = speed_threshold(take_result, "PACE_SLOW")
        high = speed_threshold(take_result, "PACE_FAST")
    elif metric == "duration_ms":
        plan = take_result.get("plan") or {}
        low, high = plan.get("min_ms"), plan.get("max_ms")
        if low is None and high is None:
            low = high = plan.get("target_ms")
    elif metric == "slide_duration_ms":
        low = high = slide_target_ms(take_result, slide_number)
    if not _is_number(low) and not _is_number(high):
        return None
    return (low if _is_number(low) else -math.inf, high if _is_number(high) else math.inf)


def _distance(value: float, bounds: tuple[float, float]) -> float:
    low, high = bounds
    return max(low - value, value - high, 0.0)


def _improved(
    metric: str,
    slide_number: int | None,
    previous: float,
    current: float,
    previous_result: dict[str, Any],
    take_result: dict[str, Any],
    cfg: ReviewConfig,
) -> bool | None:
    """좋아진 방향인지. 같으면 False. 방향 · 기준을 정할 수 없으면 None"""
    direction = cfg.comparison.direction.get(metric)
    if direction == "HIGHER":
        return current > previous
    if direction == "LOWER":
        return current < previous
    if direction == "RANGE":
        previous_bounds = _bounds(metric, slide_number, previous_result)
        current_bounds = _bounds(metric, slide_number, take_result)
        if previous_bounds is None or current_bounds is None:
            return None
        return _distance(current, current_bounds) < _distance(previous, previous_bounds)
    return None


def compare_previous(
    take_result: dict[str, Any],
    script_coverage: dict[str, Any] | None,
    previous_takes: list[dict[str, Any]] | None,
    cfg: ReviewConfig,
) -> list[dict[str, Any]]:
    """comparison. 차원 표의 지표 순서로, 지표마다 Take 값 → 장 값(장 번호순).

    script_mode: 두 Take 의 대본 보기가 다르면 {previous, current}, 같으면 None
    (모든 항목에 같이 싣는다)
    """
    if not previous_takes:
        return []
    previous = previous_takes[0]
    previous_result = previous["take_result"]

    current_mode = take_result.get("script_mode")
    previous_mode = previous_result.get("script_mode")
    script_mode = (
        None
        if current_mode == previous_mode
        else {"previous": previous_mode, "current": current_mode}
    )

    current_values = _values(take_result, (script_coverage or {}).get("take_scores"), cfg)
    previous_values = _values(previous_result, previous.get("take_scores"), cfg)

    order = {
        m: i
        for i, m in enumerate(
            dict.fromkeys(m for spec in DIMENSIONS.values() for m in spec.metrics)
        )
    }
    keys = sorted(
        current_values.keys() & previous_values.keys(),
        key=lambda k: (order.get(k[0], len(order)), k[0], k[1] is not None, k[1] or 0),
    )
    return [
        {
            "metric": metric,
            "slide_number": slide,
            "previous": previous_values[(metric, slide)],
            "current": current_values[(metric, slide)],
            "improved": _improved(
                metric,
                slide,
                previous_values[(metric, slide)],
                current_values[(metric, slide)],
                previous_result,
                take_result,
                cfg,
            ),
            "script_mode": script_mode,
        }
        for metric, slide in keys
    ]
