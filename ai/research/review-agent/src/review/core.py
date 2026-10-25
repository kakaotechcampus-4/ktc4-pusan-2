"""리뷰 진입점.

- apply_rules: 규칙 단계만 (LLM 없음, 문장 없음). 같은 입력이면 같은 결과다.
- review_take: 최종 진입점 (POST /takes/review). 규칙 단계 결과에
  LLM 단계(문장 · 다음 미션 고르기)를 붙여 응답을 만든다.
  1차는 LLM 단계가 없어 기본 문장과 후보 상위 다음 미션을 내고
  fallback_reason 을 LLM_UNAVAILABLE 로 단다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .burden import Burden, compute_burden, recurring_dimensions
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
class RuleResult:
    """규칙 단계 결과 (문장 없음).

    review_take 가 이것으로 응답을 만들고, 테스트 · 연구 평가가 근거를 본다.
    """

    take_result: dict[str, Any]
    script_coverage: dict[str, Any] | None
    evaluable: dict[str, bool]
    mission_results: list[dict[str, Any]]
    criteria_results: list[dict[str, Any]]
    burden: Burden
    dimensions: dict[str, str]
    next_mission_candidates: list[dict[str, Any]]
    recurring_issues: list[dict[str, Any]]
    score: int | None


def _validate(request: ReviewRequest | dict[str, Any]) -> ReviewRequest:
    """요청 형식이 틀리면 pydantic ValidationError (라우터가 422 로 바꾼다)"""
    return request if isinstance(request, ReviewRequest) else ReviewRequest.model_validate(request)


def criteria_versions(take_result: dict[str, Any], cfg: ReviewConfig) -> dict[str, str]:
    """meta.criteria_versions (명세 5-3).

    Take 결과 criteria 의 기능별 기준 버전 + 리뷰 자체 규칙 버전.

    키는 기준 버전의 기능 이름(``gaze-1.1+…`` → ``gaze``). 한 모듈의 기준이 여러 영역에 실려도
    (volume → VOLUME · PAUSE) 한 번만 싣는다. 영역 이름순으로 처음 본 버전을 쓴다.
    """
    versions: dict[str, str] = {}
    for area in sorted(take_result.get("criteria") or {}):
        version = (take_result["criteria"][area] or {}).get("criteria_version")
        if isinstance(version, str) and version:
            versions.setdefault(version.split("-", 1)[0], version)
    versions["review"] = cfg.criteria_version()
    return versions


def apply_rules(
    request: ReviewRequest | dict[str, Any], config: ReviewConfig | None = None
) -> RuleResult:
    """규칙 단계 (명세 6-1). LLM · 문장 없이 숫자 · 상태 · 점수 · 미션 판정 ·
    다음 미션 후보 · 반복 문제를 정한다.

    실행 순서: 판단 가능 → 미션 판정 → 부담 → 차원 상태 → 사용자 기준 → 다음 미션 후보 → 반복 문제
    → 점수. 부담 · 차원 상태가 미션 결과를 쓰므로 미션 판정을 먼저 한다.
    1차: 이전 Take 비교(6번)는 없다.
    """
    cfg = config or DEFAULT_CONFIG
    data = _validate(request).model_dump()

    take_result = data["take_result"]
    script_coverage = data["script_coverage"]
    recurring = recurring_dimensions(data["previous_recurring_issues"])
    user_criteria = data["user_criteria"] or {}
    script_used = user_criteria.get("script_used")

    evaluable = check_evaluable(take_result, script_coverage, cfg)
    mission_results = judge_missions(data["missions"] or [], take_result, script_coverage, cfg)
    burden = compute_burden(
        take_result, script_coverage, evaluable, script_used, recurring, mission_results, cfg
    )
    states = decide_dimension_states(burden, evaluable, recurring, mission_results, cfg)
    criteria_results = judge_user_criteria(user_criteria, take_result, script_coverage, cfg)
    candidates = build_next_missions(
        criteria_results, states, burden, take_result, script_coverage, cfg
    )

    return RuleResult(
        take_result=take_result,
        script_coverage=script_coverage,
        evaluable=evaluable,
        mission_results=mission_results,
        criteria_results=criteria_results,
        burden=burden,
        dimensions=states,
        next_mission_candidates=candidates,
        recurring_issues=build_recurring_issues(states, burden, cfg),
        score=compute_score(states, cfg),
    )


def review_take(
    request: ReviewRequest | dict[str, Any],
    llm: Any = None,
    *,
    model: str | None = None,
    config: ReviewConfig | None = None,
) -> ReviewResponse:
    """Take 하나를 리뷰한다 (명세 4-2). 응답을 dict 로: ``.model_dump(mode="json")``

    ``llm`` 은 LLM 출력 스키마가 걸린 객체(``.invoke`` 만 씀), 없으면 None.
    1차는 LLM 단계가 없어 ``llm`` · ``model`` 을 쓰지 않고 늘 기본 결과를 낸다.
    """
    cfg = config or DEFAULT_CONFIG
    rules = apply_rules(_validate(request), cfg)

    # 2차: LLM 이 문장을 쓰고 후보 중 고른다 (사용자 기준 FAILED 는 고정).
    # 지금은 기본 문장 · 후보 상위
    text = build_default_text(
        rules.dimensions, rules.burden, rules.take_result, rules.script_coverage, cfg
    )
    next_missions = select_next_missions(rules.next_mission_candidates, cfg)

    return ReviewResponse.model_validate(
        {
            "take_id": rules.take_result["take_id"],
            "overall_summary": text["overall_summary"],
            "score": rules.score,
            "dimensions": rules.dimensions,
            "strengths": text["strengths"],
            "improvements": text["improvements"],
            "comparison": [],  # 1차: 이전 Take 비교 없음
            "mission_results": rules.mission_results,
            "criteria_results": rules.criteria_results,
            "next_missions": next_missions,
            "recurring_issues": rules.recurring_issues,
            "fallback_reason": "LLM_UNAVAILABLE",  # 1차: LLM 단계 없음
            "meta": {
                "schema_version": SCHEMA_VERSION,
                "feature_version": FEATURE_VERSION,
                "criteria_versions": criteria_versions(rules.take_result, cfg),
                "model": None,
            },
        }
    )
