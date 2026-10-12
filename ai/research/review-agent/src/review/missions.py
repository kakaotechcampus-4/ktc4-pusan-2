"""4단계: 미션 판정 · 6단계: 사용자 기준 판정. 둘은 같은 규칙(judge_target)을 쓴다."""

from __future__ import annotations

from typing import Any

from .config import ReviewConfig
from .metrics import compare, find_metric


def judge_target(
    target: dict[str, Any],
    slide_number: int | None,
    take_result: dict[str, Any],
    script_coverage: dict[str, Any] | None,
    cfg: ReviewConfig,
) -> tuple[str, Any]:
    """target{metric, operator, value} 를 지표 이름으로 찾아 판정한다. 반환: (상태, 관측값)

    - NOT_EVALUABLE: 관측값이 없거나 측정 비율 미달 (대본 전달도는 min_scored_ratio 기준)
    - ACHIEVED: 목표 달성
    - PARTIAL: 목표 쪽으로 모자란 정도가 허용치 이내. 허용치가 없는 지표(개수 등)는 없다
    - FAILED: 그 밖
    """
    metric, operator, value = target["metric"], target["operator"], target["value"]
    area, observed, ratio = find_metric(take_result, script_coverage, metric, slide_number)

    if area == "CONTENT":
        min_ratio = cfg.evaluable.min_scored_ratio
    else:
        min_ratio = cfg.evaluable.min_measured_ratio
    if observed is None or ratio is None or ratio < min_ratio:
        return "NOT_EVALUABLE", observed

    if compare(observed, operator, value):
        return "ACHIEVED", observed

    tolerance = cfg.mission.tolerance.get(metric)
    if tolerance is None:
        return "FAILED", observed
    if operator in ("LT", "LTE"):
        gap = observed - value
    elif operator in ("GT", "GTE"):
        gap = value - observed
    else:  # EQ
        gap = abs(observed - value)
    return ("PARTIAL" if gap <= tolerance + 1e-9 else "FAILED"), observed


def judge_missions(
    missions: list[dict[str, Any]],
    take_result: dict[str, Any],
    script_coverage: dict[str, Any] | None,
    cfg: ReviewConfig,
) -> list[dict[str, Any]]:
    """이번 Take 미션 판정. 반환: mission_results"""
    results = []
    for m in missions:
        t = m["target"]
        status, observed = judge_target(t, m.get("slide_number"), take_result, script_coverage, cfg)
        results.append(
            {
                "mission_id": m["mission_id"],
                "status": status,
                "metric": t["metric"],
                "operator": t["operator"],
                "slide_number": m.get("slide_number"),
                "target": t["value"],
                "observed": observed,
            }
        )
    return results


def judge_user_criteria(
    user_criteria: dict[str, Any],
    take_result: dict[str, Any],
    script_coverage: dict[str, Any] | None,
    cfg: ReviewConfig,
) -> list[dict[str, Any]]:
    """사용자 기준(targets) 판정. 숫자 목표가 없는 기준은 건너뛴다. 반환: criteria_results"""
    results = []
    for t in user_criteria.get("targets") or []:
        if t.get("metric") is None or t.get("operator") is None or t.get("value") is None:
            continue
        status, observed = judge_target(t, t.get("slide_number"), take_result, script_coverage, cfg)
        results.append(
            {
                "display": t.get("display"),
                "metric": t["metric"],
                "operator": t["operator"],
                "target": t["value"],
                "observed": observed,
                "status": status,
            }
        )
    return results
