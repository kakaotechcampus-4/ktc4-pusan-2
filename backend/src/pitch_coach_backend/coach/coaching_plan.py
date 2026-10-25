"""코칭 계획 보관 · 고르기 (coach-agent.md 12-2).

계획 응답을 `coaching_plans` 한 행으로 만들고, 다음 Take 의 바탕 값과 같은 행을 고른다. evaluate 에
실을 계획도 여기서 만든다.
"""

import logging
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from pitch_coach_backend.coach.replies import PlanReply
from pitch_coach_backend.coach.requests import as_id, build_plan

logger = logging.getLogger(__name__)


# ── 코칭 계획 보관 · 고르기 ───────────────────────────────────────────
@dataclass(frozen=True)
class PlanBasis:
    """코칭 계획의 바탕 값 (12-2 계획 고르기). 계획 행에 두고, 다음 Take 를 시작할 때 그때 값과 모두
    같은 행만 고른다. null 끼리는 같은 값으로 본다."""

    source_take_id: str | None
    script_version_id: str | None
    target_ms: int | None
    min_ms: int | None
    max_ms: int | None


def plan_basis(
    *,
    source_take_id: str | uuid.UUID | None,
    script_version_id: str | uuid.UUID | None,
    time_limit_sec: int,
    lower_deviation_sec: int,
    upper_deviation_sec: int,
) -> PlanBasis:
    """지금 발표 상태의 바탕 값. 시간 계획은 계획을 요청할 때처럼 제한 시간으로 만든다 — Take 목표
    시간으로 만들면 목표가 제한 시간과 다를 때마다 맞는 계획이 없다."""
    plan = build_plan(
        time_limit_sec,
        time_limit_sec=time_limit_sec,
        lower_deviation_sec=lower_deviation_sec,
        upper_deviation_sec=upper_deviation_sec,
    )
    return PlanBasis(
        source_take_id=as_id(source_take_id),
        script_version_id=as_id(script_version_id),
        target_ms=plan["target_ms"],
        min_ms=plan["min_ms"],
        max_ms=plan["max_ms"],
    )


def request_basis(request: Mapping[str, Any]) -> PlanBasis:
    """계획 요청에 실은 바탕 값. 응답 값은 쓰지 않는다 — AI 는 두 id 를 그대로 돌려주게 되어 있지만,
    빠뜨리거나 다르게 돌려줘도 저장한 계획이 영영 고를 수 없는 행으로 남지 않게."""
    plan = request["plan"]
    return PlanBasis(
        source_take_id=request["source_take_id"],
        script_version_id=request["script_version_id"],
        target_ms=plan["target_ms"],
        min_ms=plan["min_ms"],
        max_ms=plan["max_ms"],
    )


def plan_payload(request: Mapping[str, Any], reply: PlanReply) -> dict[str, Any]:
    """`coaching_plans.payload` (12-1). 응답의 coaching_plan · fallback_reason · dropped ·
    planner_hash 이고 `meta` 는 따로 둔다."""
    for key in ("source_take_id", "script_version_id"):
        echoed = getattr(reply, key)
        if echoed is not None and echoed != request[key]:
            logger.warning(
                "코칭 계획 응답의 %s 가 요청과 다르다 pitch=%s", key, request.get("pitch_id")
            )
    return {
        "coaching_plan": reply.coaching_plan,
        "fallback_reason": reply.fallback_reason,
        "dropped": reply.dropped,
        "planner_hash": reply.planner_hash,
    }


def plan_for_evaluate(payload: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """고른 계획 행의 payload 에서 evaluate 에 실을 `coaching_plan`. `why` 는 뺀다 (5-2) — 사람이
    읽는 근거라 1초마다 싣지 않는다. 모양이 틀렸으면 None (계획 없이 코칭한다)."""
    if payload is None:
        return None
    coaching_plan = payload.get("coaching_plan")
    if not isinstance(coaching_plan, Mapping):
        return None
    return {
        **coaching_plan,
        "focus": [_without_why(item) for item in coaching_plan.get("focus", [])],
        "relax": [_without_why(item) for item in coaching_plan.get("relax", [])],
    }



def _without_why(item: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in item.items() if key != "why"}
