"""되돌아보기 — 개입 몇 초 뒤 실제로 행동이 바뀌었는지 보고, 다음 행동을 바꾼다.

규칙 코치와 에이전트의 차이가 여기 있습니다. 단순 규칙은 같은 말을 쿨다운마다 반복하지만,
이 코치는 결과를 보고 셋 중 하나를 고릅니다.

- 효과 있음  → 같은 방법 유지 + '좋아요, 지금처럼'(CONTINUE) 후보
- 효과 없음  → 사다리 다음 칸 (다른 방법)            STRATEGY ESCALATED
- 다 써 봄   → 그 범위에서 그만두고 리뷰로 넘긴다      STRATEGY GAVE_UP

효과는 실시간 지표로 잽니다. 리뷰는 확정 STT 로 다시 정확하게 계산할 수 있습니다.
"""

from __future__ import annotations

from typing import Any

from .candidates import Candidate
from .events import EventSink
from .schemas import OutcomeEvent, StrategyEvent
from .state import PendingOutcome, Praise, StrategyState
from .tick import Tick
from .vocab import Issue, Outcome, StrategyChange

#: 효과를 잴 때 비교하는 지표 (tick.metrics 의 키)
OUTCOME_METRIC: dict[Issue, str | None] = {
    Issue.GAZE_ON_SCRIPT: "script_ratio",
    Issue.PACE_FAST: "cpm_short",
    Issue.VOLUME_LOW: "voice_diff_db",
    Issue.FILLER_FREQUENT: "filler_count_30s",
    Issue.LONG_SILENCE: "silence_ms",
    Issue.BEHIND_SCHEDULE: "required_ratio",
    # 예상 종료는 누적값이라 10초 안에 거의 안 움직인다. '천천히'에 따라 말 속도가 줄었는지를 본다
    Issue.AHEAD_OF_SCHEDULE: "cpm_short",
    Issue.SLIDE_OVER: "slide_number",
}


def register(tick: Tick, c: Candidate, intervention_id: str) -> None:
    """방금 한 개입의 효과를 나중에 재도록 예약한다."""
    rule = tick.cfg.issues[c.issue_type]
    if c.praise is not None or rule.outcome_delay_ms is None:
        return
    metric = OUTCOME_METRIC.get(c.issue_type)
    before = _metric_avg(tick, metric)
    tick.state.pending.append(
        PendingOutcome(
            intervention_id=intervention_id,
            candidate_id=c.candidate_id,
            issue_type=c.issue_type,
            area=c.area,
            instruction=c.instruction,
            variant=c.variant,
            step=c.step,
            strategy_key=c.strategy_key,
            slide_number=c.slide_number,
            t_ms=tick.t,
            check_at_ms=tick.t + rule.outcome_delay_ms,
            metric=metric,
            before=before if isinstance(before, (int, float)) else None,
        )
    )


def resolve(tick: Tick, sink: EventSink) -> None:
    """잴 때가 된 개입의 효과를 판정하고 전략을 고친다."""
    st = tick.state
    due = [p for p in st.pending if p.check_at_ms <= tick.t]
    if not due:
        return
    st.pending = [p for p in st.pending if p.check_at_ms > tick.t]
    for pending in due:
        outcome, after = judge(tick, pending)
        sink.emit(
            OutcomeEvent,
            t_ms=tick.t,
            intervention_id=pending.intervention_id,
            candidate_id=pending.candidate_id,
            issue_type=pending.issue_type,
            area=pending.area,
            instruction=pending.instruction,
            slide_number=pending.slide_number,
            outcome=outcome,
            metric=pending.metric,
            before=pending.before,
            after=after,
        )
        _update_strategy(tick, pending, outcome, after, sink)


def prune_praise(tick: Tick) -> None:
    """만료됐거나, 교정했던 문제가 다시 나타난 격려 후보는 버린다."""
    tick.state.praise = [
        p
        for p in tick.state.praise
        if tick.t <= p.expires_ms and not tick.detected(p.source_issue_type)
    ]


def _metric_now(tick: Tick, metric: str | None) -> Any:
    if metric is None:
        return None
    if metric == "slide_number":
        return tick.slide_number
    return tick.metrics.get(metric)


#: 최근 기록에 남는 지표 — 효과 전후를 순간값이 아니라 최근 평균으로 잴 수 있다
_HISTORY_METRICS = frozenset(
    {"script_ratio", "cpm", "cpm_short", "voice_diff_db", "required_ratio"}
)


def _metric_avg(tick: Tick, metric: str | None) -> Any:
    """최근 reflection.average_ms 의 평균. 잡음으로 한 번 튄 값으로 효과를 판정하지 않는다."""
    now = _metric_now(tick, metric)
    if metric not in _HISTORY_METRICS or not isinstance(now, (int, float)):
        return now
    span = tick.cfg.reflection.average_ms
    values = [
        v
        for s in tick.history_since(tick.t - span)
        if isinstance(v := getattr(s, metric), (int, float))
    ]
    values.append(now)
    return round(sum(values) / len(values), 4)


def _threshold(tick: Tick, module: str, issue: str) -> float | None:
    """판정 모듈이 공개한 문제의 기준값. 못 읽었으면 None — 그 효과는 재지 못한 것으로 둔다."""
    found = tick.criteria.get(module, {}).get(issue)
    return found.threshold if found is not None else None


def judge(tick: Tick, p: PendingOutcome) -> tuple[Outcome, float | None]:
    cfg = tick.cfg
    rc = cfg.reflection

    if p.issue_type == Issue.SLIDE_OVER:
        moved = tick.slide_number is not None and tick.slide_number != p.slide_number
        return (Outcome.EFFECTIVE if moved else Outcome.INEFFECTIVE), tick.slide_number

    if p.issue_type == Issue.LONG_SILENCE:
        after = tick.metrics.get("silence_ms")
        samples = [s.speaking for s in tick.state.history if s.t_ms > p.t_ms] + [tick.speaking]
        known = [s for s in samples if s is not None]
        if after is None and not known:
            return Outcome.NOT_MEASURED, None
        # 지금 침묵이 개입 뒤에 시작됐으면 그 사이에 말을 했다. 단어 누락으로 '말하는 중' 표본이
        # 한 번도 안 잡혀도 알 수 있다 (실험 09)
        resumed = any(known) or (after is not None and after < tick.t - p.t_ms)
        return (Outcome.EFFECTIVE if resumed else Outcome.INEFFECTIVE), after

    after = _metric_avg(tick, p.metric)
    if not isinstance(after, (int, float)) or p.before is None:
        return Outcome.NOT_MEASURED, None
    before = p.before

    match p.issue_type:
        case Issue.GAZE_ON_SCRIPT:
            # 개입은 측정값이 잡음으로 높게 튄 순간에 일어나기 쉬워 그 뒤엔 저절로
            # 내려온다(평균으로의 회귀).
            # 그래서 '줄었다'만으로는 인정하지 않고 탐지 기준 아래로 내려와야 인정한다 (실험 03 ·
            # 14)
            ok = after < rc.gaze_back_ratio
        case Issue.PACE_FAST:
            fast = _threshold(tick, "pace", "PACE_FAST")
            if fast is None:
                return Outcome.NOT_MEASURED, float(after)
            back = after <= fast - rc.cpm_back_margin
            ok = back or after <= before * (1 - rc.cpm_drop_ratio)
        case Issue.VOLUME_LOW:
            low = _threshold(tick, "volume", "VOLUME_LOW")
            if low is None:
                return Outcome.NOT_MEASURED, float(after)
            ok = after >= low or after >= before + rc.volume_gain_db
        case Issue.FILLER_FREQUENT:
            ok = after <= before * (1 - rc.filler_drop_ratio)
        case Issue.BEHIND_SCHEDULE:
            behind = _threshold(tick, "timing", "BEHIND_SCHEDULE")
            if behind is None:
                return Outcome.NOT_MEASURED, float(after)
            ok = after < behind or after <= before - rc.schedule_delta
        case Issue.AHEAD_OF_SCHEDULE:
            end = tick.metrics.get("projected_end_ms")
            limit = tick.metrics.get("early_limit_ms")
            back_in_range = (
                isinstance(end, (int, float)) and isinstance(limit, (int, float)) and end >= limit
            )
            ok = back_in_range or after <= before * (1 - rc.cpm_drop_ratio)
        case _:
            return Outcome.NOT_MEASURED, float(after)
    return (Outcome.EFFECTIVE if ok else Outcome.INEFFECTIVE), float(after)


def _update_strategy(
    tick: Tick, p: PendingOutcome, outcome: Outcome, after: float | None, sink: EventSink
) -> None:
    st = tick.state
    cfg = tick.cfg
    rule = cfg.issues[p.issue_type]
    strat = st.strategy.setdefault(p.strategy_key, StrategyState())

    if outcome == Outcome.EFFECTIVE:
        strat.failures = 0
        if rule.praise and cfg.features.praise:
            st.praise.append(
                Praise(
                    intervention_id=p.intervention_id,
                    source_issue_type=p.issue_type,
                    area=p.area,
                    slide_number=p.slide_number,
                    intervention_t_ms=p.t_ms,
                    expires_ms=tick.t + cfg.policy.praise_ttl_ms,
                    metric=p.metric,
                    before=p.before,
                    after=after,
                )
            )
        return

    if outcome != Outcome.INEFFECTIVE:
        return

    strat.failures += 1
    current = rule.ladder[min(p.step, len(rule.ladder) - 1)]
    nxt = p.step + 1
    if nxt < len(rule.ladder):
        strat.step = max(strat.step, nxt)
        to = rule.ladder[nxt]
        sink.emit(
            StrategyEvent,
            t_ms=tick.t,
            issue_type=p.issue_type,
            area=p.area,
            slide_number=p.slide_number,
            change=StrategyChange.ESCALATED,
            from_instruction=current.instruction,
            from_variant=current.variant,
            to_instruction=to.instruction,
            to_variant=to.variant,
            failures=strat.failures,
            intervention_id=p.intervention_id,
        )
    elif rule.exhaustible and not strat.exhausted:
        strat.exhausted = True
        sink.emit(
            StrategyEvent,
            t_ms=tick.t,
            issue_type=p.issue_type,
            area=p.area,
            slide_number=p.slide_number,
            change=StrategyChange.GAVE_UP,
            from_instruction=current.instruction,
            from_variant=current.variant,
            failures=strat.failures,
            intervention_id=p.intervention_id,
        )
