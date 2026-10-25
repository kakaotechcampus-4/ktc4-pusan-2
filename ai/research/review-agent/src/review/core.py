"""리뷰 진입점.

- apply_rules: 규칙 단계만 (LLM 없음, 문장 없음). 같은 입력이면 같은 결과다.
- review_take: 최종 진입점 (POST /takes/review). 규칙 단계 결과에
  LLM 단계(문장 · 다음 미션 고르기, 1번)를 붙여 응답을 만든다.
  LLM 이 없거나 실패하면 기본 문장과 후보 상위 다음 미션을 내고 fallback_reason 을 단다.
"""

from __future__ import annotations

import copy
import logging
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from .burden import Burden, compute_burden, recurring_dimensions
from .comparison import compare_previous
from .config import DEFAULT_CONFIG, ReviewConfig
from .dimensions import decide_dimension_states
from .evaluable import check_evaluable
from .llm_input import Candidate, Candidates, build_candidates, review_message
from .missions import judge_missions, judge_user_criteria
from .next_missions import build_next_missions, select_next_missions
from .prompts.review import REVIEW_SYSTEM_PROMPT
from .recurring import build_recurring_issues
from .schemas import MAX_DESCRIPTION_CHARS, ReviewDraft, ReviewRequest, ReviewResponse
from .score import compute_score
from .text import build_default_text
from .version import FEATURE_VERSION, SCHEMA_VERSION
from .vocab import FallbackReason

log = logging.getLogger(__name__)


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
    comparison: list[dict[str, Any]]
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

    실행 순서: 판단 가능 → 미션 판정 → 부담 → 차원 상태 → 사용자 기준 → 이전 Take 비교
    → 다음 미션 후보 → 반복 문제 → 점수.
    부담 · 차원 상태가 미션 결과를 쓰므로 미션 판정을 먼저 한다.
    """
    cfg = config or DEFAULT_CONFIG
    data = _validate(request).model_dump()

    take_result = data["take_result"]
    script_coverage = data["script_coverage"]
    recurring = recurring_dimensions(data["previous_recurring_issues"])
    user_criteria = data["user_criteria"] or {}
    script_used = user_criteria.get("script_used")

    evaluable = check_evaluable(take_result, script_coverage, script_used, cfg)
    mission_results = judge_missions(data["missions"] or [], take_result, script_coverage, cfg)
    burden = compute_burden(
        take_result, script_coverage, evaluable, script_used, recurring, mission_results, cfg
    )
    states = decide_dimension_states(burden, evaluable, recurring, mission_results, cfg)
    criteria_results = judge_user_criteria(user_criteria, take_result, script_coverage, cfg)
    comparison = compare_previous(take_result, script_coverage, data["previous_takes"], cfg)
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
        comparison=comparison,
        next_mission_candidates=candidates,
        recurring_issues=build_recurring_issues(states, burden, cfg),
        score=compute_score(states, cfg),
    )


#: 예외의 code 로 받아들이는 fallback_reason (서비스 common/llm.py 가 다는 계약)
_CODES = ("LLM_TIMEOUT", "LLM_ERROR", "LLM_INVALID_OUTPUT", "LLM_UNAVAILABLE")


def _fallback_reason(exc: BaseException) -> FallbackReason:
    """LLM 호출 · 초안 읽기가 실패한 원인 (coach-agent planner 와 같은 규칙).

    예외가 네 값 중 하나인 code 를 가지면 그것을 쓴다 — 코어는 LLM 라이브러리의 예외 종류를
    import 하지 않으므로, 서비스가 시간 초과 · 출력 검증 실패 · 설정 없음 예외에 code 를 달아
    던진다. 없으면 표준 시간 초과 → LLM_TIMEOUT, pydantic 검증 실패 → LLM_INVALID_OUTPUT,
    그 밖 → LLM_ERROR.
    """
    code = getattr(exc, "code", None)
    if code in _CODES:
        return code
    if isinstance(exc, TimeoutError):
        return "LLM_TIMEOUT"
    if isinstance(exc, ValidationError):
        return "LLM_INVALID_OUTPUT"
    return "LLM_ERROR"


def _written(items: list[Any], known: dict[str, Candidate]) -> tuple[dict[str, Any], int]:
    """(후보 id → LLM 항목, 버린 항목 수). 후보에 없는 id · 같은 id 의 두 번째부터는 버린다"""
    picked: dict[str, Any] = {}
    for item in items:
        if item.candidate_id in known and item.candidate_id not in picked:
            picked[item.candidate_id] = item
    return picked, len(items) - len(picked)


def _or_default(text: str | None, default: str | None) -> str | None:
    text = (text or "").strip()
    return text or default


def apply_draft(
    draft: ReviewDraft, candidates: Candidates
) -> tuple[dict[str, Any], list[dict[str, Any]], int]:
    """LLM 초안을 후보에 붙인다 → (문장, 다음 미션, 버린 항목 수).

    - 강점 · 개선점: 어느 차원을 말할지는 규칙 단계가 정했으므로 후보를 모두 후보 순서로 낸다.
      LLM 이 쓴 문장만 바꾸고, 빠뜨렸거나 빈 문장은 기본 문장
    - 다음 미션: 사용자 기준 FAILED(fixed)를 후보 순서로 맨 앞에 두고, 이어서 LLM 이 고른 순서.
      fixed 의 설명은 사용자 표현(display)이 있으면 그대로 둔다. priority 는 1부터 다시 매긴다
    """
    strengths, dropped_s = _written(draft.strengths, candidates.strengths)
    improvements, dropped_i = _written(draft.improvements, candidates.improvements)
    missions, dropped_n = _written(draft.next_missions, candidates.next_missions)

    def texts(items: dict[str, Candidate], written: dict[str, Any]) -> list[dict[str, Any]]:
        return [
            {
                **c.default,
                "text": _or_default(getattr(written.get(cid), "text", None), c.default["text"]),
            }
            for cid, c in items.items()
        ]

    order = [cid for cid, c in candidates.next_missions.items() if c.fixed]
    order += [cid for cid in missions if not candidates.next_missions[cid].fixed]
    next_missions = []
    for priority, cid in enumerate(order, 1):
        c, item = candidates.next_missions[cid], missions.get(cid)
        m = copy.deepcopy(c.default)
        if item is not None:
            if not (c.fixed and m["description"]):
                m["description"] = _or_default(item.description, m["description"])
            m["reason"] = _or_default(item.reason, m["reason"])
        if m["description"] is not None:
            m["description"] = m["description"][:MAX_DESCRIPTION_CHARS]
        m["priority"] = priority
        next_missions.append(m)

    summary = _or_default(draft.overall_summary, None)
    text = {
        "overall_summary": summary,
        "strengths": texts(candidates.strengths, strengths),
        "improvements": texts(candidates.improvements, improvements),
    }
    return text, next_missions, dropped_s + dropped_i + dropped_n


def review_take(
    request: ReviewRequest | dict[str, Any],
    llm: Any = None,
    *,
    model: str | None = None,
    config: ReviewConfig | None = None,
) -> ReviewResponse:
    """Take 하나를 리뷰한다 (명세 4-2). 응답을 dict 로: ``.model_dump(mode="json")``

    ``llm`` 은 LLM 출력 스키마(ReviewDraft)가 걸린 객체(``.invoke(messages)`` 만 씀), 없으면 None.
    llm 이 None 이면 부르지 않고 기본 결과 + LLM_UNAVAILABLE. 호출 · 초안 읽기가 실패하면
    기본 결과 + 원인(_fallback_reason). 숫자 · 상태 · 점수 · 미션 판정은 어느 쪽이든 같다.
    ``model`` 은 응답한 모델 이름이고 LLM 결과일 때만 meta.model 에 싣는다.
    요청 형식 오류는 그대로 올린다 (라우터가 422 로 바꾼다).
    """
    cfg = config or DEFAULT_CONFIG
    rules = apply_rules(_validate(request), cfg)
    default_text = build_default_text(
        rules.dimensions, rules.burden, rules.take_result, rules.script_coverage, cfg
    )
    text = default_text
    next_missions = select_next_missions(rules.next_mission_candidates, cfg)
    reason: FallbackReason | None = "LLM_UNAVAILABLE"
    dropped = 0

    if llm is not None:
        candidates = build_candidates(rules, default_text)
        message = review_message(rules, candidates)
        try:
            out = llm.invoke([("system", REVIEW_SYSTEM_PROMPT), ("user", message)])
            draft = out if isinstance(out, ReviewDraft) else ReviewDraft.model_validate(out)
            text, next_missions, dropped = apply_draft(draft, candidates)
            reason = None
        except Exception as exc:  # noqa: BLE001 — 규칙 단계 결과가 본체라 LLM 장애로 끊지 않는다
            reason = _fallback_reason(exc)
            # 예외 메시지에 LLM 문장이 들어 있을 수 있어 종류만 남긴다 (명세 11절 로그)
            log.warning(
                "review llm failed take_id=%s reason=%s error=%s",
                rules.take_result["take_id"],
                reason,
                type(exc).__name__,
            )
        if reason is None and text["overall_summary"] is None:
            text = {**text, "overall_summary": default_text["overall_summary"]}
    # TODO 숫자 검사 (명세 6-2): 문장마다 숫자를 대조해 걸린 문장만 기본 문장으로 바꾼다

    log.info(
        "review take_id=%s score=%s dimensions=%s fallback_reason=%s dropped=%d",
        rules.take_result["take_id"],
        rules.score,
        rules.dimensions,
        reason,
        dropped,
    )
    return ReviewResponse.model_validate(
        {
            "take_id": rules.take_result["take_id"],
            "overall_summary": text["overall_summary"],
            "score": rules.score,
            "dimensions": rules.dimensions,
            "strengths": text["strengths"],
            "improvements": text["improvements"],
            "comparison": rules.comparison,
            "mission_results": rules.mission_results,
            "criteria_results": rules.criteria_results,
            "next_missions": next_missions,
            "recurring_issues": rules.recurring_issues,
            "fallback_reason": reason,
            "meta": {
                "schema_version": SCHEMA_VERSION,
                "feature_version": FEATURE_VERSION,
                "criteria_versions": criteria_versions(rules.take_result, cfg),
                "model": (model or None) if reason is None else None,
            },
        }
    )
