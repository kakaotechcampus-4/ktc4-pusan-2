"""④ 적격성 필터 — 지금 말하면 안 되는 후보를 거른다. 점수와 상관없이 무조건 적용된다.

걸린 이유는 전부 남깁니다. 하나라도 IGNORE 이유면 IGNORED, 아니고 WAIT 이유가 있으면 WAITING.
실전 모드처럼 버려진 후보도 다른 이유까지 남겨 두어야
리뷰에서 "이때 이런 게 걸렸다"를 보여줄 수 있다.
"""

from __future__ import annotations

from .candidates import Candidate
from .schemas import Mission, RelaxItem
from .tick import Tick
from .vocab import IGNORE_REASONS, WAIT_REASONS, CandidateStatus, Instruction, Issue, Mode, Reason

#: 개입 수 상한에서 빼는 시간 마무리
_WRAP_UP_ISSUES = frozenset({Issue.FINAL_MINUTE, Issue.TIME_OVER})


def _overlaps_mission(relax: RelaxItem, missions: list[Mission]) -> bool:
    """봐주기가 요청의 미션과 겹치는가. 장 번호가 null 이면 Take 전체라 모든 장과 겹친다."""
    return any(
        m.area == relax.area
        and (
            m.slide_number is None
            or relax.slide_number is None
            or m.slide_number == relax.slide_number
        )
        for m in missions
    )


def apply(tick: Tick, candidates: list[Candidate]) -> None:
    st = tick.state
    pc = tick.cfg.policy
    plan = tick.plan
    behind = tick.detected(Issue.BEHIND_SCHEDULE)

    for c in candidates:
        against: list[Reason] = []
        rule = tick.cfg.issues[c.issue_type]

        if tick.req.mode == Mode.EXAM:
            against.append(Reason.EXAM_MODE)
        if not c.sensor_ok:
            against.append(Reason.SENSOR_UNUSABLE)
        if c.confidence < pc.min_confidence:
            against.append(Reason.LOW_CONFIDENCE)
        # 대본을 보며 발표해도 되는 발표라고 알려 줬으면 대본 응시는 문제가 아니다
        if tick.req.script_used and c.issue_type == Issue.GAZE_ON_SCRIPT:
            against.append(Reason.SCRIPT_ALLOWED)
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
            r.area == c.area
            and (r.slide_number is None or r.slide_number == tick.slide_number)
            and not _overlaps_mission(r, tick.req.missions)
            for r in plan.relax
        ):
            against.append(Reason.PLAN_RELAXED)
        # 시간 마무리는 개입 상한에서 뺀다 (개입 수에는 센다)
        if (
            plan.max_interventions is not None
            and st.interventions >= plan.max_interventions
            and c.issue_type not in _WRAP_UP_ISSUES
        ):
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
