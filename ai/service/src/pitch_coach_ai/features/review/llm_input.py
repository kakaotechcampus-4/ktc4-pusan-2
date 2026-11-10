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
from .text import DIMENSION_LABELS, METRIC_LABELS, TextCandidate, TextCandidates, volume_base
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


def _focus_slides(candidates: Candidates) -> set[int]:
    """후보(강점 · 개선점 · 다음 미션)가 가리키는 장. LLM 이 장 이야기를 하는 곳은 여기뿐이다"""
    items = [c.item for c in (*candidates.strengths.values(), *candidates.improvements.values())]
    slides = {i["slide_number"] for i in items} | {
        m["slide_number"] for m in candidates.next_missions.values()
    }
    return slides - {None}


def llm_comparison(rules: RuleResult, candidates: Candidates) -> list[dict[str, Any]]:
    """LLM 에 싣는 이전 Take 비교 (명세 6-2).

    Take 값 비교(slide_number null)와 후보가 가리키는 장의 비교만 싣는다. 장이 많은 대본에서
    장마다의 비교를 모두 실으면 입력이 장 수에 비례해 커지는데,
    후보 밖의 장은 LLM 이 말할 곳이 없다.
    응답의 comparison 은 그대로 전부다 (FE 리포트 · 저장).
    """
    slides = _focus_slides(candidates)
    keys = ("metric", "slide_number", "previous", "current", "improved", "script_mode")
    return [
        {k: r[k] for k in keys}
        for r in rules.comparison
        if r["slide_number"] is None or r["slide_number"] in slides
    ]


def review_message(rules: RuleResult, candidates: Candidates) -> str:
    """LLM 에 보내는 입력. 같은 규칙 단계 결과면 같은 문자열이 나온다."""
    metrics: set[str] = set()
    comparison = llm_comparison(rules, candidates)

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
    metrics.update(r["metric"] for r in comparison)
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
        "comparison": comparison,
        "content_slides": _content_slides(rules),
        "metric_labels": {m: METRIC_LABELS[m] for m in sorted(metrics) if m in METRIC_LABELS},
    }
    if "voice_diff_db" in metrics:
        # 이번 Take 의 소리 크기를 견준 때 ('점검 때' · '발표 처음' · '기준', #1181)
        payload["volume_base"] = volume_base(rules.take_result)
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)
