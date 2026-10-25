"""LLM 단계의 입력 (명세 6-2). 규칙 단계 결과를 후보와 LLM 메시지로 바꾼다.

- 강점 · 개선점 후보는 기본 문장(text.build_default_text)의 항목 그대로다. 영역 · 장 · 근거는
  규칙 단계가 정했고, LLM 은 candidate_id 로 가리켜 문장만 쓴다.
- 다음 미션 후보는 규칙 단계 후보(next_missions.build_next_missions) 그대로다.
  앞쪽의 사용자 기준 FAILED 미션은 fixed — LLM 이 빼거나 순서를 바꾸지 못한다 (core 가 다시 맞춘다).
- 대본 원문 · STT 원문은 넣지 않는다.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .text import DIMENSION_LABELS, METRIC_LABELS
from .vocab import CONTENT_BURDEN_STATUSES, METRIC_TO_DIMENSION

if TYPE_CHECKING:
    from .core import RuleResult


@dataclass(frozen=True)
class Candidate:
    """후보 하나. default 는 LLM 이 없거나 문장을 못 쓸 때 그대로 내는 응답 항목"""

    dimension: str | None
    #: 강점 후보의 차원 상태 (STRENGTH · IMPROVED). 그 밖은 None
    state: str | None
    default: dict[str, Any]
    #: 다음 미션 중 사용자 기준 FAILED 미션
    fixed: bool = False


@dataclass(frozen=True)
class Candidates:
    """candidate_id → 후보. dict 순서가 후보 순서다 (s1 · i1 · n1 부터)"""

    strengths: dict[str, Candidate]
    improvements: dict[str, Candidate]
    next_missions: dict[str, Candidate]


def build_candidates(rules: RuleResult, default_text: dict[str, Any]) -> Candidates:
    """기본 문장 · 다음 미션 후보에 candidate_id 를 붙인다.

    기본 문장의 강점은 STRENGTH · IMPROVED 차원을 차원 표 순서로, 개선점은 반복 문제와 같은
    PRIORITY 차원 순서로 만든다 (text.build_default_text). 그 순서로 차원을 짝짓는다.
    """
    good = [(d, s) for d, s in rules.dimensions.items() if s in ("STRENGTH", "IMPROVED")]
    strengths = {
        f"s{i}": Candidate(dimension=d, state=s, default=item)
        for i, ((d, s), item) in enumerate(zip(good, default_text["strengths"], strict=True), 1)
    }
    improvements = {
        f"i{i}": Candidate(dimension=issue["dimension"], state=None, default=item)
        for i, (issue, item) in enumerate(
            zip(rules.recurring_issues, default_text["improvements"], strict=True), 1
        )
    }
    # build_next_missions 는 사용자 기준 FAILED 를 모두 앞에 넣는다
    n_fixed = sum(r["status"] == "FAILED" for r in rules.criteria_results)
    next_missions = {
        f"n{i}": Candidate(
            dimension=METRIC_TO_DIMENSION.get(m["target"]["metric"]),
            state=None,
            default=m,
            fixed=i <= n_fixed,
        )
        for i, m in enumerate(rules.next_mission_candidates, 1)
    }
    return Candidates(strengths, improvements, next_missions)


def _content_slides(rules: RuleResult) -> list[dict[str, Any]]:
    """장마다 빠뜨린 · 다르게 말한 핵심 내용 개수. 내용 전달을 판단할 수 없으면 빈 목록"""
    if rules.dimensions.get("content_delivery") == "NOT_EVALUABLE" or not rules.script_coverage:
        return []
    slides = []
    for ev in rules.script_coverage.get("evaluations") or []:
        counts = dict.fromkeys(sorted(CONTENT_BURDEN_STATUSES), 0)
        for kp in ev.get("key_points") or []:
            if kp.get("status") in counts:
                counts[kp["status"]] += 1
        if any(counts.values()):
            slides.append({"slide_number": ev["slide_number"], **counts})
    return slides


def review_message(rules: RuleResult, candidates: Candidates) -> str:
    """LLM 에 보내는 입력. 같은 규칙 단계 결과면 같은 문자열이 나온다."""
    metrics: set[str] = set()

    def text_candidates(items: dict[str, Candidate]) -> list[dict[str, Any]]:
        out = []
        for cid, c in items.items():
            metrics.update(c.default["evidence"])
            out.append(
                {
                    "candidate_id": cid,
                    "label": DIMENSION_LABELS.get(c.dimension, c.dimension),
                    **({"state": c.state} if c.state else {}),
                    "area": c.default["area"],
                    "slide_number": c.default["slide_number"],
                    "evidence": c.default["evidence"],
                }
            )
        return out

    next_missions = []
    for cid, c in candidates.next_missions.items():
        m = c.default
        metrics.add(m["target"]["metric"])
        next_missions.append(
            {
                "candidate_id": cid,
                "fixed": c.fixed,
                "area": m["area"],
                "slide_number": m["slide_number"],
                "target": m["target"],
                "description": m["description"],
                "reason": m["reason"],
            }
        )

    metrics.update(r["metric"] for r in rules.mission_results)
    metrics.update(r["metric"] for r in rules.criteria_results)
    metrics.update(r["metric"] for r in rules.comparison)
    payload: dict[str, Any] = {
        "score": rules.score,
        "dimensions": [
            {"label": DIMENSION_LABELS[d], "state": s} for d, s in rules.dimensions.items()
        ],
        "strength_candidates": text_candidates(candidates.strengths),
        "improvement_candidates": text_candidates(candidates.improvements),
        "next_mission_candidates": next_missions,
        "mission_results": [
            {
                k: r[k]
                for k in ("metric", "operator", "slide_number", "target", "observed", "status")
            }
            for r in rules.mission_results
        ],
        "criteria_results": [
            {k: r[k] for k in ("display", "metric", "operator", "target", "observed", "status")}
            for r in rules.criteria_results
        ],
        "comparison": [
            {k: r[k] for k in ("metric", "slide_number", "previous", "current", "improved")}
            for r in rules.comparison
        ],
        "content_slides": _content_slides(rules),
        "metric_labels": {m: METRIC_LABELS[m] for m in sorted(metrics) if m in METRIC_LABELS},
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)
