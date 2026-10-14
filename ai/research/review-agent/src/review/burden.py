"""2단계: 문제 부담. 차원별로 문제가 얼마나 심했는지를 숫자 하나로 만든다.

부담 = 믿을 수 있는 문제 구간의 mean_severity × 초 (+ 대본 전달도의 빠진 · 틀린 핵심 내용).
차원 합계(total)로 PRIORITY 순위를 매기고, 장별 합계(by_slide)로 장 미션 · 반복 문제의 장을,
영역별 합계(by_area)로 영역이 여럿인 차원(pacing_rhythm)의 반복 문제 영역을 고른다.
"""

from __future__ import annotations

from typing import Any

from .config import ReviewConfig
from .evaluable import measured_areas
from .vocab import (
    AREA_TO_DIMENSIONS,
    CONTENT_BURDEN_STATUSES,
    DIMENSIONS,
    ISSUE_TO_DIMENSION,
    METRIC_TO_DIMENSION,
    NON_BURDEN_ISSUES,
    SLIDE_ONLY_DIMENSIONS,
)

#: {dimension: {"total": float, "by_slide": {slide_number: float}, "by_area": {area: float}}}
Burden = dict[str, dict[str, Any]]


def recurring_dimensions(recurring_issues: list[dict[str, Any]] | None) -> set[str]:
    """반복 문제가 가리키는 차원들.

    dimension 이 있으면 그 차원, 없으면(예전 리뷰) area 에 속한 차원 전부 (GAZE 는 두 차원).
    결과는 포함 여부 확인에만 쓴다 (순서를 정하지 않는다).
    """
    dims: set[str] = set()
    for it in recurring_issues or []:
        if it.get("dimension") in DIMENSIONS:
            dims.add(it["dimension"])
        else:
            dims.update(AREA_TO_DIMENSIONS.get(it.get("area"), ()))
    return dims


def _add(burden: Burden, dim: str, area: str, slide_number: int | None, amount: float) -> None:
    b = burden[dim]
    b["total"] += amount
    b["by_slide"][slide_number] = b["by_slide"].get(slide_number, 0.0) + amount
    b["by_area"][area] = b["by_area"].get(area, 0.0) + amount


def compute_burden(
    take_result: dict[str, Any],
    script_coverage: dict[str, Any] | None,
    evaluable: dict[str, bool],
    script_used: bool | None,
    previous_recurring_issues: list[dict[str, Any]] | None,
    mission_results: list[dict[str, Any]],
    cfg: ReviewConfig,
) -> Burden:
    """차원별 부담.

    - reliable 이 아닌 구간 · NON_BURDEN_ISSUES · 판단 불가 차원 · 판단 불가 영역은 세지 않는다
      (pacing_rhythm 은 TIME 만 못 재도 SPEED · PAUSE 구간은 센다)
    - script_used 가 true 면 GAZE_ON_SCRIPT 를 세지 않는다
    - content_delivery: missing · contradicted 핵심 내용(key_points)을 importance 별 부담으로
    - 반복 문제 · gave_up · 미션을 이루지 못한 차원은 이유마다 배수를 한 번씩 곱한다
    """
    c = cfg.burden
    burden: Burden = {dim: {"total": 0.0, "by_slide": {}, "by_area": {}} for dim in DIMENSIONS}

    # 1) 문제 구간
    measured = measured_areas(take_result)
    for seg in take_result.get("problem_segments") or []:
        issue = seg["issue_type"]
        if not seg.get("reliable"):
            continue  # 측정을 믿을 수 없던 구간
        if issue in NON_BURDEN_ISSUES:
            continue  # 정상 행동일 수 있는 문제
        if script_used and issue == "GAZE_ON_SCRIPT":
            continue  # 사용자가 대본을 보기로 함
        dim = ISSUE_TO_DIMENSION.get(issue)
        if dim is None or not evaluable[dim]:
            continue
        if seg["area"] not in measured:
            continue  # 차원은 판단 가능해도 이 영역은 못 잼
        seconds = max(0, seg["end_ms"] - seg["start_ms"]) / 1000
        _add(burden, dim, seg["area"], seg.get("slide_number"), seg["mean_severity"] * seconds)

    # 2) 대본 전달도 (content_delivery)
    if evaluable["content_delivery"] and script_coverage is not None:
        fallback = c.content_burden[c.content_burden_fallback]
        for ev in script_coverage.get("evaluations") or []:
            for kp in ev.get("key_points") or []:
                if kp.get("status") in CONTENT_BURDEN_STATUSES:
                    # importance 가 없거나 모르는 값이면 fallback
                    # (입력 하나로 리뷰 전체가 실패하지 않게)
                    amount = c.content_burden.get(kp.get("importance"), fallback)
                    _add(burden, "content_delivery", "CONTENT", ev["slide_number"], amount)

    # 3) 가중: 차원마다 이유별로 한 번씩 곱한다
    weights = {dim: 1.0 for dim in DIMENSIONS}
    for dim in recurring_dimensions(previous_recurring_issues):
        weights[dim] *= c.recurring_weight
    gave_up = {ISSUE_TO_DIMENSION.get(g["issue_type"]) for g in take_result.get("gave_up") or []}
    for dim in gave_up - {None}:
        weights[dim] *= c.gave_up_weight
    not_achieved = {
        METRIC_TO_DIMENSION.get(m["metric"])
        for m in mission_results
        if m["status"] in ("FAILED", "PARTIAL")
    }
    for dim in not_achieved - {None}:
        weights[dim] *= c.mission_failed_weight

    for dim, w in weights.items():
        if w != 1.0:
            b = burden[dim]
            b["total"] *= w
            b["by_slide"] = {k: v * w for k, v in b["by_slide"].items()}
            b["by_area"] = {k: v * w for k, v in b["by_area"].items()}
    return burden


def focus_slide(dim: str, burden: Burden, cfg: ReviewConfig) -> int | None:
    """부담이 몰린 장. 장 미션 · 반복 문제의 장을 정한다. 몰리지 않았으면 None (Take 전체).

    - 부담이 가장 큰 장(같으면 번호가 작은 장)이 전체의 slide_concentration 을 넘으면 그 장
    - 내용(content_delivery)은 늘 장 단위라 가장 큰 장
    """
    by_slide = {k: v for k, v in burden[dim]["by_slide"].items() if k is not None}
    total = burden[dim]["total"]
    if not by_slide or total <= 0:
        return None
    slide, amount = max(by_slide.items(), key=lambda kv: (kv[1], -kv[0]))
    if dim in SLIDE_ONLY_DIMENSIONS:
        return slide
    return slide if amount / total > cfg.next_mission.slide_concentration else None
