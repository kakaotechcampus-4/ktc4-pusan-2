"""2단계: 문제 부담. 차원별로 문제가 얼마나 심했는지를 숫자 하나로 만든다.

부담 = 믿을 수 있는 문제 구간의 mean_severity × 초 (+ 대본 전달도의 빠진 · 틀린 핵심 내용).
차원 합계(total)로 PRIORITY 순위를 매기고, 영역별 합계(by_area)로 다음 미션 · 반복 문제의 영역을,
그 영역의 문제별 · 장별 합계(by_area_issue)로 문제와 장 미션 · 반복 문제의 장을 고른다 (focus).
"""

from __future__ import annotations

import logging
from typing import Any

from .config import ReviewConfig
from .evaluable import measured_areas
from .vocab import (
    CONTENT_BURDEN_STATUSES,
    CONTENT_ISSUE,
    DIMENSIONS,
    ISSUE_TO_DIMENSION,
    METRIC_TO_DIMENSION,
    NON_BURDEN_ISSUES,
    SLIDE_ONLY_ISSUES,
    TAKE_ONLY_ISSUES,
)

log = logging.getLogger(__name__)

#: {dimension: {"total": float, "by_slide": {slide_number: float}, "by_area": {area: float},
#:              "by_area_issue": {area: {issue: {slide_number: float}}}}}
#: 내용(content_delivery)은 문제 이름이 없어 문제 자리에 CONTENT_ISSUE 를 쓴다
Burden = dict[str, dict[str, Any]]


def recurring_dimensions(recurring_issues: list[dict[str, Any]] | None) -> set[str]:
    """반복 문제가 가리키는 차원들.

    dimension 이 없거나 차원 이름이 아닌 항목은 반복 판정에 쓰지 않고 로그에 남긴다
    (그 항목 때문에 리뷰 전체를 거절하지 않는다, 명세 5-2).
    결과는 포함 여부 확인에만 쓴다 (순서를 정하지 않는다).
    """
    dims: set[str] = set()
    skipped = 0
    for it in recurring_issues or []:
        if it.get("dimension") in DIMENSIONS:
            dims.add(it["dimension"])
        else:
            skipped += 1
    if skipped:
        log.warning(
            "previous_recurring_issues: dimension 이 없거나 모르는 항목 %d개를 건너뜀", skipped
        )
    return dims


def _add(
    burden: Burden, dim: str, area: str, issue: str, slide_number: int | None, amount: float
) -> None:
    b = burden[dim]
    b["total"] += amount
    b["by_slide"][slide_number] = b["by_slide"].get(slide_number, 0.0) + amount
    b["by_area"][area] = b["by_area"].get(area, 0.0) + amount
    issue_slide = b["by_area_issue"].setdefault(area, {}).setdefault(issue, {})
    issue_slide[slide_number] = issue_slide.get(slide_number, 0.0) + amount


def compute_burden(
    take_result: dict[str, Any],
    script_coverage: dict[str, Any] | None,
    evaluable: dict[str, bool],
    script_used: bool | None,
    recurring_dims: set[str],
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
    burden: Burden = {
        dim: {"total": 0.0, "by_slide": {}, "by_area": {}, "by_area_issue": {}}
        for dim in DIMENSIONS
    }

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
        amount = seg["mean_severity"] * seconds
        _add(burden, dim, seg["area"], issue, seg.get("slide_number"), amount)

    # 2) 대본 전달도 (content_delivery)
    if evaluable["content_delivery"] and script_coverage is not None:
        fallback = c.content_burden[c.content_burden_fallback]
        for ev in script_coverage.get("evaluations") or []:
            for kp in ev.get("key_points") or []:
                if kp.get("status") in CONTENT_BURDEN_STATUSES:
                    # importance 가 없거나 모르는 값이면 fallback
                    # (입력 하나로 리뷰 전체가 실패하지 않게)
                    amount = c.content_burden.get(kp.get("importance"), fallback)
                    _add(
                        burden,
                        "content_delivery",
                        "CONTENT",
                        CONTENT_ISSUE,
                        ev["slide_number"],
                        amount,
                    )

    # 3) 가중: 차원마다 이유별로 한 번씩 곱한다
    weights = {dim: 1.0 for dim in DIMENSIONS}
    for dim in recurring_dims:
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
            b["by_area_issue"] = {
                a: {i: {k: v * w for k, v in s.items()} for i, s in issues.items()}
                for a, issues in b["by_area_issue"].items()
            }
    return burden


def focus_area(dim: str, burden: Burden) -> str:
    """차원에서 부담이 가장 큰 영역 (같으면 차원 표에 적힌 순서)"""
    areas = DIMENSIONS[dim].areas
    by_area = burden[dim]["by_area"]
    return max(areas, key=lambda a: (by_area.get(a, 0.0), -areas.index(a)))


def focus_issue(dim: str, area: str, burden: Burden) -> str | None:
    """고른 영역에서 부담이 가장 큰 문제 (같으면 차원 표에 적힌 순서). 부담이 없으면 None"""
    issues = burden[dim]["by_area_issue"].get(area, {})
    if not issues:
        return None
    order = [*DIMENSIONS[dim].issues, CONTENT_ISSUE]
    return max(issues, key=lambda i: (sum(issues[i].values()), -order.index(i)))


def focus_slide(
    dim: str, area: str, issue: str | None, burden: Burden, cfg: ReviewConfig
) -> int | None:
    """고른 문제에서 부담이 몰린 장. 몰리지 않았으면 None (Take 전체).

    같은 영역의 다른 문제 부담은 섞지 않는다 (TIME_OVER 부담이 SLIDE_OVER 의 장을 바꾸지 않게).

    - 그 문제에서 부담이 가장 큰 장(같으면 번호가 작은 장)이
      그 문제 부담의 slide_concentration 을 넘으면 그 장
    - 시간 · 내용(SLIDE_ONLY_ISSUES)은 늘 장 단위라 가장 큰 장
    - Take 전체의 시간 초과(TAKE_ONLY_ISSUES)는 늘 None
    """
    if issue is None or issue in TAKE_ONLY_ISSUES:
        return None
    issue_slide = burden[dim]["by_area_issue"].get(area, {}).get(issue, {})
    by_slide = {k: v for k, v in issue_slide.items() if k is not None}
    total = sum(issue_slide.values())
    if not by_slide or total <= 0:
        return None
    slide, amount = max(by_slide.items(), key=lambda kv: (kv[1], -kv[0]))
    if issue in SLIDE_ONLY_ISSUES:
        return slide
    return slide if amount / total > cfg.next_mission.slide_concentration else None


def focus(dim: str, burden: Burden, cfg: ReviewConfig) -> tuple[str, str | None, int | None]:
    """(영역, 문제, 장). 다음 미션 후보 · 반복 문제 · 개선점이 같이 쓴다 (명세 6-1 7 · 8번)"""
    area = focus_area(dim, burden)
    issue = focus_issue(dim, area, burden)
    return area, issue, focus_slide(dim, area, issue, burden, cfg)
