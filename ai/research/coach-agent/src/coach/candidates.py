"""③ 후보 생성 — 문제(Detection)를 '할 수 있는 행동'으로 바꾼다.

문제와 행동은 1:1 이 아닙니다. 같은 문제라도 전략 사다리의 몇 번째 칸인지(앞선 개입이
통했는지), 지금 속도로 따라잡을 수 있는지에 따라 다른 행동이 나옵니다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .state import Praise, StrategyState, episode_key, strategy_key
from .tick import Tick
from .vocab import (
    ISSUE_TYPE,
    CandidateStatus,
    FeedbackType,
    Instruction,
    Issue,
    Reason,
)


@dataclass
class Candidate:
    candidate_id: str
    issue_type: Issue
    area: FeedbackType
    instruction: Instruction
    variant: str
    #: 사다리에서 쓴 칸
    step: int
    #: 사다리 단계 · 포기 · 개입 횟수의 키. 사다리를 나눠 쓰는 문제는 같다
    strategy_key: str
    #: 문제 구간 · 참은 기록의 키. 문제마다 다르다
    episode_key: str
    severity: float
    confidence: float
    persistence_ms: int
    slide_number: int | None
    evidence: dict[str, Any]
    params: dict[str, Any]
    metric: str | None
    sensor_ok: bool = True
    #: 유지 격려 후보면 그 근거가 된 개입
    praise: Praise | None = None
    reasons_for: list[Reason] = field(default_factory=list)
    reasons_against: list[Reason] = field(default_factory=list)
    score: float = 0.0
    status: CandidateStatus | None = None

    @property
    def priority(self) -> int:
        return round(min(1.0, max(0.0, self.score)) * 100)

    @property
    def eligible(self) -> bool:
        return self.status is None


def build(tick: Tick) -> list[Candidate]:
    out: list[Candidate] = []
    st = tick.state
    for det in tick.detections:
        rule = tick.cfg.issues[det.issue_type]
        if rule.record_only:
            continue  # 문제 구간(episodes)에만 남기고 말하지 않는다
        ekey = episode_key(det.issue_type, det.slide_number)
        key = strategy_key(det.issue_type, det.slide_number)
        episode = st.episodes[ekey]  # episodes.observe() 가 먼저 열어 둔다
        strat = st.strategy.get(key) or StrategyState()
        step = min(max(strat.step, det.min_step), len(rule.ladder) - 1)
        chosen = rule.ladder[step]

        reasons: list[Reason] = []
        if strat.step > 0:
            reasons.append(Reason.ESCALATED)
        if det.min_step > 0:
            reasons.append(Reason.SPEED_LIMIT_EXCEEDED)

        out.append(
            Candidate(
                candidate_id=episode.candidate_id,
                issue_type=det.issue_type,
                area=ISSUE_TYPE[det.issue_type],
                instruction=chosen.instruction,
                variant=chosen.variant,
                step=step,
                strategy_key=key,
                episode_key=ekey,
                severity=det.severity,
                confidence=det.confidence,
                # 믿을 수 있는 상태로 이어진 시간. 믿을 수 없으면 0 이라 NOT_PERSISTENT 로 기다린다
                persistence_ms=(
                    tick.t - episode.reliable_since_ms
                    if episode.reliable_since_ms is not None
                    else 0
                ),
                slide_number=det.slide_number,
                evidence={
                    "start_ms": episode.start_ms,
                    "end_ms": tick.t,
                    "slide_number": det.slide_number,
                    **det.evidence,
                },
                params=dict(det.params),
                metric=det.metric,
                sensor_ok=det.sensor_ok,
                reasons_for=reasons,
            )
        )

    if tick.cfg.features.praise:
        out.extend(_praise_candidates(tick))
    return out


def _praise_candidates(tick: Tick) -> list[Candidate]:
    """효과가 있던 교정 → '좋아요, 지금처럼' 후보. 우선순위는 늘 가장 낮다."""
    out: list[Candidate] = []
    for praise in tick.state.praise:
        out.append(
            Candidate(
                candidate_id=f"{Issue.IMPROVED_AFTER_FEEDBACK.value}-{praise.intervention_id}",
                issue_type=Issue.IMPROVED_AFTER_FEEDBACK,
                area=praise.area,
                instruction=Instruction.CONTINUE,
                variant="default",
                step=0,
                strategy_key=Issue.IMPROVED_AFTER_FEEDBACK.value,
                episode_key=Issue.IMPROVED_AFTER_FEEDBACK.value,
                severity=tick.cfg.policy.praise_severity,
                confidence=1.0,
                persistence_ms=0,
                slide_number=tick.slide_number,
                evidence={
                    "start_ms": praise.intervention_t_ms,
                    "end_ms": tick.t,
                    "slide_number": tick.slide_number,
                    "source_intervention_id": praise.intervention_id,
                    "metric": praise.metric,
                    "before": praise.before,
                    "after": praise.after,
                },
                params={},
                metric=None,
                praise=praise,
                reasons_for=[Reason.AFTER_EFFECTIVE_FEEDBACK],
            )
        )
    return out
