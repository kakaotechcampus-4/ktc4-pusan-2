"""LLM 단계의 입력 (명세 6-2). 규칙 단계 결과를 후보와 LLM 메시지로 바꾼다.

- 강점 · 개선점 후보는 text.text_candidates 그대로다. 영역 · 장 · 근거는 규칙 단계가 정했고,
  LLM 은 candidate_id 로 가리켜 문장만 쓴다.
- 다음 미션 후보는 규칙 단계 후보(next_missions.build_next_missions) 그대로다.
  앞쪽의 사용자 기준 FAILED 미션은 fixed — LLM 이 빼거나 순서를 바꾸지 못한다
  (next_missions.merge_picked_missions 가 다시 맞춘다).
- 대본 원문 · STT 원문은 넣지 않는다.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .next_missions import fixed_count
from .text import DIMENSION_LABELS, METRIC_LABELS, TextCandidate, TextCandidates
from .vocab import CONTENT_BURDEN_STATUSES

if TYPE_CHECKING:
    from .core import RuleResult


@dataclass(frozen=True)
class Candidates:
    """candidate_id → 후보. dict 순서가 후보 순서다 (s1 · i1 · n1 부터)"""

    strengths: dict[str, TextCandidate]
    improvements: dict[str, TextCandidate]
    #: 다음 미션 후보 (build_next_missions 출력 그대로)
    next_missions: dict[str, dict[str, Any]]
    #: next_missions 맨 앞의 사용자 기준 FAILED 미션 개수
    n_fixed: int


def build_candidates(rules: RuleResult, texts: TextCandidates) -> Candidates:
    """강점 · 개선점 · 다음 미션 후보에 candidate_id 를 붙인다."""
    return Candidates(
        strengths={f"s{i}": c for i, c in enumerate(texts.strengths, 1)},
        improvements={f"i{i}": c for i, c in enumerate(texts.improvements, 1)},
        next_missions={f"n{i}": m for i, m in enumerate(rules.next_mission_candidates, 1)},
        n_fixed=fixed_count(rules.criteria_results),
    )


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

    def text_candidates(items: dict[str, TextCandidate]) -> list[dict[str, Any]]:
        out = []
        for cid, c in items.items():
            metrics.update(c.item["evidence"])
            out.append(
                {
                    "candidate_id": cid,
                    "label": DIMENSION_LABELS[c.dimension],
                    "state": c.state,
                    "area": c.item["area"],
                    "slide_number": c.item["slide_number"],
                    "evidence": c.item["evidence"],
                }
            )
        return out

    next_missions = []
    for i, (cid, m) in enumerate(candidates.next_missions.items()):
        metrics.add(m["target"]["metric"])
        next_missions.append(
            {
                "candidate_id": cid,
                "fixed": i < candidates.n_fixed,
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
