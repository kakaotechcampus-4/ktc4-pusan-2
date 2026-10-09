"""⑤ 우선순위 — 후보마다 점수를 매긴다.

    priority = 100 × 심각도 × 신뢰도 × 지속 × 미션 × 반복 × 계획 × 시간 × 악화 × 새로움

가중치를 곱할 때마다 '왜'를 reasons_for 에 남깁니다. 그대로 응답의 reason_codes 가 됩니다.
"""

from __future__ import annotations

from .candidates import Candidate
from .schemas import MissionTarget
from .tick import Tick
from .vocab import FeedbackType, Reason


def score(tick: Tick, c: Candidate) -> None:
    pc = tick.cfg.policy
    weight = 1.0

    if c.praise is None:
        weight *= _persistence(tick, c)
        weight *= _mission(tick, c)
        weight *= _recurrence(tick, c)
        weight *= _plan_focus(tick, c)
        weight *= _time(tick, c)
        weight *= _worsening(tick, c)

    fired = tick.state.fires_by_issue.get(c.issue_type.value, 0)
    weight *= max(pc.novelty_floor, 1.0 - pc.novelty_decay * fired)

    c.score = c.severity * c.confidence * weight


def _add(c: Candidate, reason: Reason) -> None:
    if reason not in c.reasons_for:
        c.reasons_for.append(reason)


def _persistence(tick: Tick, c: Candidate) -> float:
    pc = tick.cfg.policy
    extra = c.persistence_ms - tick.cfg.issues[c.issue_type].persistence_ms
    if extra <= 0:
        return 1.0
    if extra >= pc.persistence_bonus_full_ms / 3:
        _add(c, Reason.PERSISTENT)
    return 1.0 + pc.persistence_bonus_max * min(1.0, extra / pc.persistence_bonus_full_ms)


def _mission(tick: Tick, c: Candidate) -> float:
    pc = tick.cfg.policy
    weight = 1.0
    for mission in tick.req.missions:
        if mission.area != c.area:
            continue
        if mission.slide_number is not None and mission.slide_number != tick.slide_number:
            continue
        weight = max(weight, pc.mission_weight)
        _add(c, Reason.MISSION_RELEVANT)
        if mission.target is not None and violates(
            tick.metrics.get(mission.target.metric), mission.target
        ):
            weight = max(weight, pc.mission_at_risk_weight)
            _add(c, Reason.MISSION_AT_RISK)
    return weight


def violates(value: object, target: MissionTarget) -> bool:
    """지금 값이 미션 목표를 벗어나 있는가. 값이 없으면 판단하지 않는다."""
    if not isinstance(value, (int, float)):
        return False
    match target.operator:
        case "LT":
            return not value < target.value
        case "LTE":
            return not value <= target.value
        case "GT":
            return not value > target.value
        case "GTE":
            return not value >= target.value
        case "EQ":
            return value != target.value
    return False


def _recurrence(tick: Tick, c: Candidate) -> float:
    for issue in tick.req.memory.recurring_issues:
        if issue.area == c.area and (
            issue.slide_number is None or issue.slide_number == tick.slide_number
        ):
            _add(c, Reason.RECURRING)
            return tick.cfg.policy.recurrence_weight
    return 1.0


def _plan_focus(tick: Tick, c: Candidate) -> float:
    pc = tick.cfg.policy
    weight = 1.0
    for focus in tick.state.plan.focus:
        if focus.area == c.area and (
            focus.slide_number is None or focus.slide_number == tick.slide_number
        ):
            w = min(pc.plan_weight_max, max(pc.plan_weight_min, focus.weight))
            weight *= w
            if w > 1.0:
                _add(c, Reason.PLAN_FOCUS)
    return weight


def _time(tick: Tick, c: Candidate) -> float:
    """발표 막바지일수록 시간 안내가 다른 지적보다 앞선다."""
    target = tick.req.plan.target_ms
    if c.area != FeedbackType.TIME or not target:
        return 1.0
    remaining = tick.metrics.get("remaining_ms")
    if isinstance(remaining, (int, float)) and remaining <= tick.cfg.timing.final_minute_ms:
        _add(c, Reason.TIME_CRITICAL)
    frac = min(1.0, max(0.0, tick.t / target))
    return 1.0 + (tick.cfg.policy.time_weight_max - 1.0) * frac


def _worsening(tick: Tick, c: Candidate) -> float:
    pc = tick.cfg.policy
    if c.metric is None:
        return 1.0
    delta = pc.worsening_delta.get(c.metric)
    now = tick.metrics.get(c.metric)
    past_sample = tick.sample_at_or_before(tick.t - pc.worsening_window_ms)
    past = getattr(past_sample, c.metric, None) if past_sample is not None else None
    if delta is None or not isinstance(now, (int, float)) or not isinstance(past, (int, float)):
        return 1.0
    diff = now - past
    if (delta > 0 and diff >= delta) or (delta < 0 and diff <= delta):
        _add(c, Reason.WORSENING)
        return pc.worsening_weight
    return 1.0
