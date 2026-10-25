"""7단계: 다음 미션. 규칙 단계가 후보와 목표 숫자를 만든다.

2차에서는 LLM 이 PRIORITY 후보 중 고르고 설명을 붙인다.
사용자 기준 FAILED 미션은 LLM 이 빼거나 순서를 바꾸지 못한다.
LLM 이 없거나 실패하면 select_next_missions 가 후보를 그대로 쓴다.
"""

from __future__ import annotations

import copy
from typing import Any

from .burden import Burden, focus
from .config import ReviewConfig
from .metrics import find_metric, slide_target_ms, speed_threshold
from .schemas import MAX_DESCRIPTION_CHARS, MAX_NEXT_MISSIONS
from .text import METRIC_LABELS, OPERATOR_WORDS
from .vocab import DIMENSION_ORDER, METRIC_TO_DIMENSION


def _format_value(metric: str, value: float) -> str:
    """비율 지표는 %, 시간 지표는 분 · 초, 개수 지표는 번, 나머지는 그대로"""
    if metric.endswith("_ratio") or metric == "content_coverage":
        return f"{round(value * 100)}%"
    if metric.endswith("_ms"):
        minutes, seconds = divmod(round(value / 1000), 60)
        if not minutes:
            return f"{seconds}초"
        return f"{minutes}분 {seconds}초" if seconds else f"{minutes}분"
    if metric.endswith("_count"):
        return f"{value:g}번"
    return f"{value:g}"


def _describe(metric: str, operator: str, value: float, slide_number: int | None) -> str:
    where = f"{slide_number}번 장 " if slide_number is not None else ""
    label = METRIC_LABELS.get(metric, metric)
    return f"{where}{label} {_format_value(metric, value)} {OPERATOR_WORDS[operator]}".strip()


def _final(
    metric: str,
    operator: str,
    slide_number: int | None,
    take_result: dict[str, Any],
    cfg: ReviewConfig,
) -> Any:
    """최종 목표. 기준이 Take 마다 다른 지표는 그 Take 의 기준이다 (명세 6-1 7번). 없으면 None

    - cpm: LTE 면 PACE_FAST, GTE 면 PACE_SLOW 기준값
    - slide_duration_ms: 그 장의 target_ms
    - duration_ms: plan.max_ms (TIME_OVER 의 Take 미션)
    """
    if metric == "cpm":
        return speed_threshold(take_result, "PACE_FAST" if operator == "LTE" else "PACE_SLOW")
    if metric == "slide_duration_ms":
        return slide_target_ms(take_result, slide_number)
    if metric == "duration_ms":
        return (take_result.get("plan") or {}).get("max_ms") if operator == "LTE" else None
    return cfg.next_mission.final.get(metric)


def _next_target(operator: str, current: float, step: Any, final: Any) -> float | None:
    """지금 값에서 한 걸음 나아진 목표. 최종 목표를 넘지 않는다. 이미 최종 목표면 None"""
    if step is None or final is None:
        return None
    if operator == "LTE":  # 낮을수록 좋음
        return None if current <= final else max(current - step, final)
    return None if current >= final else min(current + step, final)


def build_next_missions(
    criteria_results: list[dict[str, Any]],
    dimension_states: dict[str, str],
    burden: Burden,
    take_result: dict[str, Any],
    script_coverage: dict[str, Any] | None,
    cfg: ReviewConfig,
) -> list[dict[str, Any]]:
    """다음 미션 후보 (priority 1부터).

    합계 MAX_NEXT_MISSIONS 개 이하, 설명 MAX_DESCRIPTION_CHARS 자 이하 (명세 5-3 · 6-1 7번).

    1) 사용자 기준 FAILED: 사용자가 정한 목표 그대로, 모두 넣는다
       (targets 한도가 상한 이하라 다 들어간다).
       지표가 속한 차원의 부담 큰 순, 같으면 targets 순서
    2) PRIORITY 차원마다 하나, priority_candidate_max 개까지이고 합계 상한을 넘지 않게
       (부담 큰 순, 같으면 차원 표 순서).
       부담이 가장 큰 영역의 부담이 가장 큰 문제로 mission_target 에서 지표 · 방향을 찾는다.
       목표 = 지금 값에서 step 만큼 나아진 값 (최종 목표를 넘지 않음, _final).
       그 문제의 부담이 몰린 장이 있으면 장 미션, 아니면 Take 미션
       (반복 문제와 같은 영역 · 장, focus).
       표에 없는 문제이거나, 같은 (지표, 장)이 이미 있거나, 지금 값(장 미션이면 그 장 값)이 없거나,
       최종 목표를 정할 기준이 없거나 이미 최종 목표면 건너뛴다. 건너뛰어도 반복 문제는 낸다
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
    room = min(c.priority_candidate_max, MAX_NEXT_MISSIONS - len(missions))
    priority_dims = [d for d, s in dimension_states.items() if s == "PRIORITY"]
    priority_dims.sort(key=lambda d: (-burden[d]["total"], DIMENSION_ORDER[d]))
    for dim in priority_dims:
        if room <= 0:
            break
        _, issue, slide = focus(dim, burden, cfg)
        rule = c.mission_target.get(issue) if issue is not None else None
        if rule is None:
            continue
        metric, operator = rule.metric, rule.operator
        area, current, _ = find_metric(take_result, script_coverage, metric, slide)
        if current is None or (metric, slide) in taken:
            continue
        final = _final(metric, operator, slide, take_result, cfg)
        value = _next_target(operator, current, c.step.get(metric), final)
        if value is None:
            continue
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
        room -= 1

    missions = missions[:MAX_NEXT_MISSIONS]
    for i, m in enumerate(missions, 1):
        m["priority"] = i
        if m["description"] is not None:
            m["description"] = m["description"][:MAX_DESCRIPTION_CHARS]
    return missions


def select_next_missions(
    candidates: list[dict[str, Any]], cfg: ReviewConfig
) -> list[dict[str, Any]]:
    """LLM 없이 고르기 (1차 · LLM 실패 시): 후보 전부 (이미 상한 안).

    priority 를 1부터 다시 매긴다.
    """
    picked = copy.deepcopy(candidates)
    for i, m in enumerate(picked, 1):
        m["priority"] = i
    return picked
