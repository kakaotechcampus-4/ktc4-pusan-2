"""7단계: 다음 미션. 규칙 단계가 후보와 목표 숫자를 만들고, 최종 몇 개를 고른다.

2차에서는 LLM 이 후보 중 고르고 설명을 붙인다.
사용자 기준 FAILED 미션은 LLM 이 빼거나 순서를 바꾸지 못한다.
LLM 이 없거나 실패하면 select_next_missions 가 후보 상위를 쓴다.
"""

from __future__ import annotations

import copy
from typing import Any

from .burden import Burden, focus_slide
from .config import ReviewConfig
from .metrics import find_metric
from .text import METRIC_LABELS, OPERATOR_WORDS
from .vocab import METRIC_TO_DIMENSION, SLIDE_ONLY_DIMENSIONS


def _format_value(metric: str, value: float) -> str:
    """비율 지표는 %, 나머지는 그대로"""
    if metric.endswith("_ratio") or metric == "content_coverage":
        return f"{round(value * 100)}%"
    return f"{value:g}"


def _describe(metric: str, operator: str, value: float, slide_number: int | None) -> str:
    where = f"{slide_number}번 장 " if slide_number is not None else ""
    label = METRIC_LABELS.get(metric, metric)
    return f"{where}{label} {_format_value(metric, value)} {OPERATOR_WORDS[operator]}".strip()


def _next_target(metric: str, current: float, cfg: ReviewConfig) -> tuple[str, float] | None:
    """지금 값에서 한 걸음 나아진 목표. 최종 목표를 넘지 않는다. 이미 최종 목표면 None"""
    step = cfg.next_mission.step.get(metric)
    final = cfg.next_mission.final.get(metric)
    if step is None or final is None:
        return None
    if step < 0:  # 낮을수록 좋음
        if current <= final:
            return None
        return "LTE", max(current + step, final)
    if current >= final:  # 높을수록 좋음
        return None
    return "GTE", min(current + step, final)


def build_next_missions(
    criteria_results: list[dict[str, Any]],
    dimension_states: dict[str, str],
    burden: Burden,
    take_result: dict[str, Any],
    script_coverage: dict[str, Any] | None,
    cfg: ReviewConfig,
) -> list[dict[str, Any]]:
    """다음 미션 후보 (candidate_max 개까지, priority 1부터).

    1) 사용자 기준 FAILED: 사용자가 정한 목표 그대로.
       지표가 속한 차원의 부담 큰 순, 같으면 targets 순서
    2) PRIORITY 차원마다 하나 (부담 큰 순, 같으면 이름순).
       mission_metric 이 None 인 차원은 건너뛴다.
       목표 = 지금 값 + step (final 을 넘지 않음).
       부담이 몰린 장이 있으면 장 미션, 그 장 값이 없으면 Take 미션.
       같은 (지표, 장)이 이미 있거나 지금 값이 없거나 이미 최종 목표면 건너뛴다
    """
    c = cfg.next_mission
    missions: list[dict[str, Any]] = []
    taken: set[tuple[str, int | None]] = set()  # (지표, 장) 중복 막기 — 확인용

    # 1) 사용자 기준 FAILED
    def criteria_burden(item: dict[str, Any]) -> float:
        dim = METRIC_TO_DIMENSION.get(item["metric"])
        return burden[dim]["total"] if dim else 0.0

    failed = [r for r in criteria_results if r["status"] == "FAILED"]
    failed.sort(key=lambda r: -criteria_burden(r))  # 안정 정렬: 부담이 같으면 입력 순서
    for r in failed:
        area, _, _ = find_metric(take_result, script_coverage, r["metric"])
        missions.append(
            {
                "area": area,
                "slide_number": None,
                "description": r["display"],
                "target": {"metric": r["metric"], "operator": r["operator"], "value": r["target"]},
                "reason": f"직접 정한 기준인데 이번 Take {r['observed']:g}",
            }
        )
        taken.add((r["metric"], None))

    # 2) PRIORITY 차원
    priority_dims = [d for d, s in dimension_states.items() if s == "PRIORITY"]
    priority_dims.sort(key=lambda d: (-burden[d]["total"], d))
    for dim in priority_dims:
        metric = c.mission_metric.get(dim)
        if metric is None:
            continue
        slide = focus_slide(dim, burden, cfg)
        area, current, _ = find_metric(take_result, script_coverage, metric, slide)
        if current is None and slide is not None and dim not in SLIDE_ONLY_DIMENSIONS:
            slide = None  # 장 값이 없으면 Take 미션으로
            area, current, _ = find_metric(take_result, script_coverage, metric)
        if current is None or (metric, slide) in taken:
            continue
        nxt = _next_target(metric, current, cfg)
        if nxt is None:
            continue
        operator, value = nxt
        value = round(value, 2) if isinstance(value, float) else value
        where = f"{slide}번 장 " if slide is not None else ""
        missions.append(
            {
                "area": area,
                "slide_number": slide,
                "description": _describe(metric, operator, value, slide),
                "target": {"metric": metric, "operator": operator, "value": value},
                "reason": f"{where}이번 Take {current:g}",
            }
        )
        taken.add((metric, slide))

    missions = missions[: c.candidate_max]
    for i, m in enumerate(missions, 1):
        m["priority"] = i
    return missions


def select_next_missions(
    candidates: list[dict[str, Any]], cfg: ReviewConfig
) -> list[dict[str, Any]]:
    """LLM 없이 고르기 (1차 · LLM 실패 시): 후보 상위 max_count 개, priority 를 1부터 다시 매긴다"""
    picked = copy.deepcopy(candidates[: cfg.next_mission.max_count])
    for i, m in enumerate(picked, 1):
        m["priority"] = i
    return picked
