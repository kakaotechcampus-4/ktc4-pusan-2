"""④ 적격성 필터 — 지금 말하면 안 되는 후보를 거른다. 점수와 상관없이 무조건 적용된다.

걸린 이유는 전부 남깁니다. 하나라도 IGNORE 이유면 IGNORED, 아니고 WAIT 이유가 있으면 WAITING.
실전 모드처럼 버려진 후보도 다른 이유까지 남겨 두어야
리뷰에서 "이때 이런 게 걸렸다"를 보여줄 수 있다.
"""

from __future__ import annotations

from .candidates import Candidate
from .evaluators.base import Tick
from .vocab import IGNORE_REASONS, WAIT_REASONS, CandidateStatus, Instruction, Issue, Mode, Reason


def apply(tick: Tick, candidates: list[Candidate]) -> None:
    st = tick.state
    pc = tick.cfg.policy
    plan = st.plan
    behind = tick.detected(Issue.BEHIND_SCHEDULE)

    for c in candidates:
        against: list[Reason] = []
        rule = tick.cfg.issues[c.issue]

        if tick.req.mode == Mode.EXAM:
            against.append(Reason.EXAM_MODE)
        if not c.sensor_ok:
            against.append(Reason.SENSOR_UNUSABLE)
        if c.confidence < pc.min_confidence:
            against.append(Reason.LOW_CONFIDENCE)
        # 늦는 중인데 '천천히'는 시간을 더 모자라게 만든다
        if behind and c.instruction == Instruction.SLOW_DOWN:
            against.append(Reason.TIME_PRESSURE)
        if c.praise is None:
            strat = st.strategy.get(c.strategy_key)
            if strat is not None and strat.exhausted:
                against.append(Reason.STRATEGY_EXHAUSTED)
            if rule.max_fires is not None and strat is not None and strat.fires >= rule.max_fires:
                against.append(Reason.ALREADY_DELIVERED)
        if any(
            r.type == c.type and (r.slide_number is None or r.slide_number == tick.slide_number)
            for r in plan.relax
        ):
            against.append(Reason.PLAN_RELAXED)
        if plan.max_interventions is not None and st.interventions >= plan.max_interventions:
            against.append(Reason.BUDGET_EXHAUSTED)

        if c.persistence_ms < rule.persistence_ms:
            against.append(Reason.NOT_PERSISTENT)
        if st.last_fired_ms is not None and tick.t - st.last_fired_ms < pc.min_gap_ms:
            against.append(Reason.MIN_GAP)
        last = st.last_by_instruction.get(c.instruction.value)
        if last is not None and tick.t - last < pc.cooldown_ms:
            against.append(Reason.COOLDOWN)

        c.reasons_against = against
        if any(r in IGNORE_REASONS for r in against):
            c.status = CandidateStatus.IGNORED
        elif any(r in WAIT_REASONS for r in against):
            c.status = CandidateStatus.WAITING
        else:
            c.status = None
