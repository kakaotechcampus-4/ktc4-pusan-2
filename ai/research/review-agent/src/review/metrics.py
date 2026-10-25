"""지표 찾기와 비교. 미션 판정 · 다음 미션 · 근거 문장이 같이 쓴다.

요청은 core 에서 검증한 뒤 dict(model_dump)로 넘어온다.
"""

from __future__ import annotations

from typing import Any


def find_metric(
    take_result: dict[str, Any],
    script_coverage: dict[str, Any] | None,
    metric: str,
    slide_number: int | None = None,
) -> tuple[str | None, Any, float | None]:
    """지표 이름으로 값을 찾는다. Take 결과 → 없으면 대본 전달도. slide_number 가 있으면 장 값.

    반환: (영역, 값, 측정 비율). 못 찾으면 값이 None.
    대본 전달도는 Take 값의 측정 비율이 scored_ratio, 채점된 장은 1.0, 채점에 실패한 장은 0.0 이다.
    """
    # 1) Take 결과: 지표 이름은 영역을 통틀어 하나뿐이라 처음 찾은 영역이 그 지표의 영역
    for area, data in take_result["areas"].items():
        take_metrics = data.get("take") or {}  # 측정 비율 미달 영역은 take 가 null
        slides = data.get("slides") or []
        slide_has = any(metric in (s.get("metrics") or {}) for s in slides)
        if metric not in take_metrics and not slide_has:
            continue
        if slide_number is None:
            return area, take_metrics.get(metric), data.get("measured_ratio")
        for s in slides:
            if s["slide_number"] == slide_number:
                return area, (s.get("metrics") or {}).get(metric), s.get("measured_ratio")
        return area, None, None  # 그 장 값이 없음

    # 2) 대본 전달도 (CONTENT)
    if script_coverage is None:
        return None, None, None
    if slide_number is None:
        take_scores = script_coverage.get("take_scores") or {}
        if metric in take_scores:
            return "CONTENT", take_scores[metric], take_scores.get("scored_ratio")
        return None, None, None
    failed = {
        f["slide_number"] if isinstance(f, dict) else f for f in script_coverage.get("failed") or []
    }
    if slide_number in failed:
        return "CONTENT", None, 0.0
    for ev in script_coverage.get("evaluations") or []:
        if ev["slide_number"] == slide_number and metric in (ev.get("scores") or {}):
            return "CONTENT", ev["scores"][metric], 1.0
    return None, None, None


def speed_threshold(take_result: dict[str, Any], issue: str) -> Any:
    """그 Take 를 판정한 말 속도 기준값 (criteria.SPEED.issues.<PACE_FAST · PACE_SLOW>.threshold)"""
    issues = ((take_result.get("criteria") or {}).get("SPEED") or {}).get("issues") or {}
    return (issues.get(issue) or {}).get("threshold")


def slide_target_ms(take_result: dict[str, Any], slide_number: int | None) -> Any:
    """그 장의 목표 시간 (areas.TIME.slides[].target_ms). 없으면 None"""
    slides = (take_result["areas"].get("TIME") or {}).get("slides") or []
    return next((s.get("target_ms") for s in slides if s["slide_number"] == slide_number), None)


def compare(observed: float, operator: str, target: float) -> bool:
    """observed operator target 이 참인지"""
    if operator == "LT":
        return observed < target
    if operator == "LTE":
        return observed <= target
    if operator == "GT":
        return observed > target
    if operator == "GTE":
        return observed >= target
    if operator == "EQ":
        return abs(observed - target) < 1e-9
    raise ValueError(f"unknown operator: {operator}")
