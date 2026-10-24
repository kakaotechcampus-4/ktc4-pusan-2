"""Take 시작 전 코칭 계획 — LLM 이 대본 · 미션 · 직전 Take 결과 · 이전 리뷰를 읽고 이번 Take 에서
집중 · 완화할 것을 정한다.

1초 판단은 지금처럼 규칙이 한다. 계획은 우선순위 가중치(focus, priority.py)와 참을 이유
(relax · 개입 상한, eligibility.py)로만 반영된다. 매초 LLM 을 부르지 않고 Take 당 한 번만 부른다.

코어는 LLM 클라이언트를 만들지 않는다. 출력 스키마가 PlanDraft 로 고정된 LLM(`.invoke(messages)`)과
캐시를 인자로 받는다. LLM 이 없거나 실패하면 기본 계획(아무것도 바꾸지 않음)으로 돌아가고 이유를
fallback_reason 으로 알린다 — 계획을 못 세웠다고 코칭이 멈추면 안 된다. LLM 이 낸 계획은 그대로
쓰지 않고 config.planner 범위로 검증하고 자른다.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from typing import Any, Protocol

from pydantic import ValidationError

from .config import DEFAULT_CONFIG, CoachConfig, PlannerConfig
from .prompts.plan import PLAN_SYSTEM_PROMPT
from .schemas import (
    CoachingPlan,
    FocusItem,
    PlanDraft,
    PlanFallbackReason,
    PlanMeta,
    PlanRequest,
    PlanResponse,
    RelaxItem,
)

log = logging.getLogger(__name__)

#: why 문장 길이 상한 — 계획은 요청의 coaching_plan 으로 매초 오간다
_WHY_CHARS = 200
#: 대본 속 수치 (12억 · 18.2% · 2024년 · 1,240명 → 숫자 하나씩)
_NUMBER = re.compile(r"\d+(?:[.,]\d+)*")


class PlanCache(Protocol):
    """LLM 응답 캐시. key 는 메시지 해시, planner_hash 는 프롬프트 · 스키마 · 모델 해시다."""

    def get(self, key: str, planner_hash: str) -> str | None: ...

    def put(self, key: str, planner_hash: str, output_json: str) -> None: ...


def planner_hash(model: str) -> str:
    """프롬프트 · 출력 스키마 · 모델이 바뀌면 달라지는 해시. 다르면 캐시를 쓰지 않는다."""
    payload = {
        "prompt": PLAN_SYSTEM_PROMPT,
        "schema": PlanDraft.model_json_schema(),
        "model": model,
    }
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def plan_message(req: PlanRequest, cfg: PlannerConfig) -> str:
    """LLM 에 보내는 입력. 같은 요청이면 같은 문자열이 나온다 (캐시 키)."""
    scripts = {s.slide_number: s.script for s in req.scripts}
    slides = [
        {
            "slide_number": s.slide_number,
            "target_seconds": round(s.target_ms / 1000),
            "script": scripts.get(s.slide_number, "")[: cfg.max_script_chars],
            # 수치가 몰린 장을 LLM 이 대본을 읽다 놓치지 않게 센 값을 함께 준다
            "number_count": len(_NUMBER.findall(scripts.get(s.slide_number, ""))),
        }
        for s in sorted(req.plan.slides, key=lambda s: s.slide_number)
    ]
    payload: dict[str, Any] = {
        "target_seconds": round(req.plan.target_ms / 1000) if req.plan.target_ms else None,
        "slides": slides,
        "missions": [
            {
                "type": m.area.value,
                "slide_number": m.slide_number,
                "description": m.description,
                "target": m.target.model_dump() if m.target else None,
            }
            for m in req.missions
        ],
        "recurring_issues": [
            {"type": r.area.value, "slide_number": r.slide_number} for r in req.recurring_issues
        ],
        "previous_review": _previous_review(req.previous_review, cfg),
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _previous_review(review: dict[str, Any] | None, cfg: PlannerConfig) -> dict[str, Any] | None:
    """직전 리뷰 근거에서 계획에 필요한 것만 — 순위 매긴 문제, 영역 상태, 코칭 요약."""
    if not review:
        return None
    issues = [
        {
            "rank": issue.get("rank"),
            "type": issue.get("area"),
            "slide_number": issue.get("slide_number"),
            "burden_s": issue.get("burden_s"),
            "memory": issue.get("memory"),
            "gave_up": issue.get("gave_up"),
        }
        for issue in (review.get("issues") or [])[: cfg.max_previous_issues]
        if isinstance(issue, dict)
    ]
    status = {
        s.get("area"): s.get("status")
        for s in (review.get("type_status") or [])
        if isinstance(s, dict)
    }
    summary = review.get("summary") if isinstance(review.get("summary"), dict) else {}
    return {
        "issues": issues,
        "type_status": status,
        "interventions": summary.get("interventions"),
        "effective_rate": summary.get("effective_rate"),
    }


def validate_draft(
    draft: PlanDraft, req: PlanRequest, cfg: CoachConfig
) -> tuple[CoachingPlan, list[str]]:
    """LLM 초안을 코치가 쓸 계획으로. 범위를 벗어난 항목은 빼고 이유를 남긴다."""
    pc, pol = cfg.planner, cfg.policy
    slides = {s.slide_number for s in req.plan.slides}
    dropped: list[str] = []

    focus: list[FocusItem] = []
    for f in draft.focus:
        label = f"focus {f.type.value} {f.slide_number if f.slide_number is not None else '전체'}"
        if f.slide_number is not None and f.slide_number not in slides:
            dropped.append(f"{label}: 계획에 없는 장")
        elif any(x.area == f.type and x.slide_number == f.slide_number for x in focus):
            dropped.append(f"{label}: 중복")
        elif len(focus) >= pc.max_focus:
            dropped.append(f"{label}: {pc.max_focus}개 초과")
        else:
            weight = min(pol.plan_weight_max, max(pol.plan_weight_min, f.weight))
            focus.append(
                FocusItem(
                    area=f.type,
                    slide_number=f.slide_number,
                    weight=round(weight, 2),
                    why=f.why[:_WHY_CHARS],
                )
            )

    mission_scopes = {(m.area, m.slide_number) for m in req.missions}
    relax: list[RelaxItem] = []
    for r in draft.relax:
        label = f"relax {r.type.value} {r.slide_number}"
        if r.type not in pc.relax_types:
            dropped.append(f"{label}: 봐줄 수 없는 영역")
        elif r.slide_number not in slides:
            dropped.append(f"{label}: 계획에 없는 장")
        elif (r.type, r.slide_number) in mission_scopes or (r.type, None) in mission_scopes:
            dropped.append(f"{label}: 이번 미션 영역")
        elif any(f.area == r.type and f.slide_number in (None, r.slide_number) for f in focus):
            dropped.append(f"{label}: 집중 영역과 겹침")
        elif any(x.area == r.type and x.slide_number == r.slide_number for x in relax):
            dropped.append(f"{label}: 중복")
        elif len(relax) >= pc.max_relax:
            dropped.append(f"{label}: {pc.max_relax}개 초과")
        else:
            relax.append(
                RelaxItem(area=r.type, slide_number=r.slide_number, why=r.why[:_WHY_CHARS])
            )

    budget = _budget(draft.max_interventions, req, pc, dropped)
    plan = CoachingPlan(focus=focus, relax=relax, max_interventions=budget)
    return plan, dropped


def _budget(
    budget: int | None, req: PlanRequest, pc: PlannerConfig, dropped: list[str]
) -> int | None:
    """개입 상한은 직전 Take 보다 적게 말하게 할 때만 뜻이 있다.

    직전 리뷰가 없으면 코칭이 지나쳤다는 근거가 없고, 하한(min_interventions)으로 올린 상한이
    직전 Take 의 개입 수 이상이면 아무것도 줄이지 못한다 — 둘 다 상한을 두지 않는다.
    """
    if budget is None:
        return None
    label = f"max_interventions {budget}"
    summary = (req.previous_review or {}).get("summary")
    before = summary.get("interventions") if isinstance(summary, dict) else None
    if not isinstance(before, int):
        dropped.append(f"{label}: 직전 리뷰의 개입 수가 없어 근거 없음")
        return None
    if budget < pc.min_interventions:
        dropped.append(f"{label}: {pc.min_interventions} 으로 올림")
        budget = pc.min_interventions
    if budget >= before:
        dropped.append(f"{label}: 직전 Take 개입 {before}번보다 줄이지 못함")
        return None
    return budget


#: 예외의 code 로 받아들이는 fallback_reason (서비스 common/llm.py 가 다는 계약)
_CODES = ("LLM_TIMEOUT", "LLM_ERROR", "LLM_INVALID_OUTPUT", "LLM_UNAVAILABLE")


def _fallback_reason(exc: BaseException) -> PlanFallbackReason:
    """LLM 호출 · 초안 읽기가 실패한 원인(6-4 5).

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


def plan_coaching(
    request: PlanRequest | dict[str, Any],
    *,
    llm: Any = None,
    model: str = "",
    cache: PlanCache | None = None,
    config: CoachConfig | None = None,
) -> PlanResponse:
    """리뷰 저장 뒤 한 번. 코칭 계획을 coaching_plan 으로 돌려준다.

    BE 가 응답을 발표에 보관했다가 다음 Take 의 매 /coach/evaluate 요청의 coaching_plan 에 싣는다.
    요청에 모드가 없다 — 계획은 Take 전에 만들고 실전 모드는 evaluate 에서 말하지 않는다.

    llm 이 None 이면 LLM 을 부르지 않고 기본 계획 + LLM_UNAVAILABLE. LLM 호출 · 초안 읽기 ·
    검증이 실패하면 기본 계획 + 원인(LLM_TIMEOUT · LLM_ERROR · LLM_INVALID_OUTPUT). 요청의
    source_take_id · script_version_id 는 그대로 돌려주고, meta.model 은 성공했을 때만 model 이다.
    요청 형식 오류는 그대로 올린다 (API 가 422 로 바꾼다).
    """
    cfg = config or DEFAULT_CONFIG
    req = request if isinstance(request, PlanRequest) else PlanRequest.model_validate(request)
    phash = planner_hash(model)

    def respond(
        plan: CoachingPlan,
        reason: PlanFallbackReason | None = None,
        dropped: list[str] | None = None,
    ) -> PlanResponse:
        return PlanResponse(
            coaching_plan=plan,
            source_take_id=req.source_take_id,
            script_version_id=req.script_version_id,
            fallback_reason=reason,
            dropped=dropped or [],
            planner_hash=phash,
            meta=PlanMeta(model=(model or None) if reason is None else None),
        )

    if llm is None:
        return respond(CoachingPlan(), "LLM_UNAVAILABLE")

    message = plan_message(req, cfg.planner)
    key = hashlib.sha256(message.encode("utf-8")).hexdigest()
    draft = _cached(cache, key, phash)
    if draft is None:
        try:
            out = llm.invoke([("system", PLAN_SYSTEM_PROMPT), ("user", message)])
            draft = out if isinstance(out, PlanDraft) else PlanDraft.model_validate(out)
        except Exception as exc:  # noqa: BLE001 — 계획이 없어도 코칭은 기본 계획으로 계속한다
            reason = _fallback_reason(exc)
            log.exception("coach plan failed pitch_id=%s reason=%s", req.pitch_id, reason)
            return respond(CoachingPlan(), reason)
        if cache is not None:
            try:
                cache.put(key, phash, draft.model_dump_json())
            except Exception:  # noqa: BLE001 — 저장하지 못해도 이번 계획은 쓴다
                log.warning("coach plan cache put failed pitch_id=%s", req.pitch_id, exc_info=True)
    try:
        plan, dropped = validate_draft(draft, req, cfg)
    except Exception:  # noqa: BLE001 — 검증이 깨져도 기본 계획으로 돌려 코칭을 막지 않는다
        log.exception("coach plan validation failed pitch_id=%s", req.pitch_id)
        return respond(CoachingPlan(), "LLM_INVALID_OUTPUT")
    return respond(plan, None, dropped)


def _cached(cache: PlanCache | None, key: str, phash: str) -> PlanDraft | None:
    """캐시에 있는 초안. 캐시를 읽지 못하거나 저장된 값이 깨졌으면 없는 것으로 본다."""
    if cache is None:
        return None
    try:
        raw = cache.get(key, phash)
        return PlanDraft.model_validate_json(raw) if raw is not None else None
    except Exception:  # noqa: BLE001 — 캐시 문제로 계획을 못 세우면 안 된다
        log.warning("coach plan cache get failed", exc_info=True)
        return None
