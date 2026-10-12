"""리뷰의 유일한 진입점: review_take (POST /takes/review).

규칙 단계는 LLM 없이 돌고, 같은 입력이면 같은 결과다. 1차는 LLM 단계가 없어
기본 문장과 후보 상위 다음 미션을 내고 fallback_reason 을 LLM_UNAVAILABLE 로 단다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .burden import Burden, compute_burden
from .config import DEFAULT_CONFIG, ReviewConfig
from .dimensions import decide_dimension_states
from .evaluable import check_evaluable
from .missions import judge_missions, judge_user_criteria
from .next_missions import build_next_missions, select_next_missions
from .recurring import build_recurring_issues
from .schemas import ReviewRequest, ReviewResponse
from .score import compute_score
from .text import build_default_text
from .version import FEATURE_VERSION, SCHEMA_VERSION


@dataclass(frozen=True)
class ReviewTrace:
    """규칙 단계의 중간 값. 응답에는 없고 research · 테스트에서 근거를 볼 때 쓴다"""

    evaluable: dict[str, bool]
    burden: Burden
    next_mission_candidates: list[dict[str, Any]]


def review_take_traced(
    request: ReviewRequest | dict[str, Any], config: ReviewConfig | None = None
) -> tuple[ReviewResponse, ReviewTrace]:
    """review_take 와 같고 중간 값을 함께 돌려준다.

    실행 순서: 판단 가능 → 미션 판정 → 부담 → 차원 상태 → 사용자 기준 → 다음 미션 → 반복 문제
    → 점수 → 기본 문장. 부담 · 차원 상태가 미션 결과를 쓰므로 미션 판정을 먼저 한다
    (명세 단계 번호와 다르다).
    요청 형식이 틀리면 pydantic ValidationError (라우터가 422 로 바꾼다).
    """
    cfg = config or DEFAULT_CONFIG
    req = request if isinstance(request, ReviewRequest) else ReviewRequest.model_validate(request)
    data = req.model_dump()

    take_result = data["take_result"]
    script_coverage = data["script_coverage"]
    prev_recurring = data["previous_recurring_issues"] or []
    user_criteria = data["user_criteria"] or {}
    script_used = user_criteria.get("script_used")

    evaluable = check_evaluable(take_result, script_coverage, cfg)
    mission_results = judge_missions(data["missions"] or [], take_result, script_coverage, cfg)
    burden = compute_burden(
        take_result, script_coverage, evaluable, script_used, prev_recurring, mission_results, cfg
    )
    states = decide_dimension_states(burden, evaluable, prev_recurring, mission_results, cfg)
    criteria_results = judge_user_criteria(user_criteria, take_result, script_coverage, cfg)
    candidates = build_next_missions(
        criteria_results, states, burden, take_result, script_coverage, cfg
    )
    # 2차: LLM 이 후보 중 고른다 (사용자 기준 FAILED 는 고정). 지금은 후보 상위
    next_missions = select_next_missions(candidates, cfg)
    recurring_issues = build_recurring_issues(states, burden, cfg)
    score = compute_score(states, cfg)
    text = build_default_text(states, burden, take_result, script_coverage, cfg)

    response = ReviewResponse.model_validate(
        {
            "take_id": take_result["take_id"],
            "overall_summary": text["overall_summary"],
            "score": score,
            "dimensions": states,
            "strengths": text["strengths"],
            "improvements": text["improvements"],
            "comparison": [],  # 1차: 이전 Take 비교(5단계) 없음
            "mission_results": mission_results,
            "criteria_results": criteria_results,
            "next_missions": next_missions,
            "recurring_issues": recurring_issues,
            "fallback_reason": "LLM_UNAVAILABLE",  # 1차: LLM 단계 없음
            "meta": {
                "schema_version": SCHEMA_VERSION,
                "feature_version": FEATURE_VERSION,
                "criteria_versions": {"review": cfg.criteria_version()},
                "model": None,
            },
        }
    )
    trace = ReviewTrace(evaluable=evaluable, burden=burden, next_mission_candidates=candidates)
    return response, trace


def review_take(
    request: ReviewRequest | dict[str, Any], config: ReviewConfig | None = None
) -> ReviewResponse:
    """Take 하나를 리뷰한다 (명세 3). 응답을 dict 로: ``.model_dump(mode="json")``"""
    return review_take_traced(request, config)[0]
