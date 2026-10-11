"""⑥ 행동 선택 — WAIT / IGNORE / INTERVENE 를 정한다.

설계 그림의 '코치 에이전트' 상자가 이 자리입니다. v1 은 규칙(RulePolicy)이고,
v2 에서 같은 인터페이스(select)로 LLM 선택기를 끼웁니다. LLM 선택기도 이 함수가 받는
후보 안에서만 고를 수 있고, 실패하면 RulePolicy 의 결과로 돌아갑니다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from .candidates import Candidate
from .evaluators.base import Tick
from .state import Hold
from .vocab import (
    IGNORE_REASONS,
    ISSUE_ORDER,
    WAIT_REASONS,
    Action,
    CandidateStatus,
    Reason,
)


@dataclass
class Selection:
    action: Action
    selected: Candidate | None
    reason_codes: list[str] = field(default_factory=list)
    candidate_id: str | None = None


class Policy(Protocol):
    name: str

    def select(self, tick: Tick, candidates: list[Candidate]) -> Selection: ...


def rank_key(c: Candidate) -> tuple[float, int, str]:
    return (-c.score, ISSUE_ORDER.index(c.issue), c.candidate_id)


def _unique(reasons: list[Reason], allowed: frozenset[Reason]) -> list[str]:
    out: list[str] = []
    for r in reasons:
        if r in allowed and r.value not in out:
            out.append(r.value)
    return out


class RulePolicy:
    name = "rule"

    def select(self, tick: Tick, candidates: list[Candidate]) -> Selection:
        pc = tick.cfg.policy
        st = tick.state

        if not candidates:
            st.hold = None
            return Selection(Action.WAIT, None, [Reason.NO_CANDIDATE.value])

        ranked = sorted(candidates, key=rank_key)
        eligible = [c for c in ranked if c.eligible]

        if not eligible:
            st.hold = None
            waiting = [c for c in ranked if c.status == CandidateStatus.WAITING]
            if waiting:
                top = waiting[0]
                return Selection(
                    Action.WAIT, None, _unique(top.reasons_against, WAIT_REASONS), top.candidate_id
                )
            top = ranked[0]
            return Selection(
                Action.IGNORE, None, _unique(top.reasons_against, IGNORE_REASONS), top.candidate_id
            )

        top = eligible[0]
        if top.priority < pc.intervene_threshold:
            st.hold = None
            for c in eligible:
                c.status = CandidateStatus.IGNORED
                c.reasons_against.append(Reason.LOW_PRIORITY)
            return Selection(Action.IGNORE, None, [Reason.LOW_PRIORITY.value], top.candidate_id)

        # 말하는 도중에 끼어들지 않는다 — 문장이 끝날 때까지 최대 pause_wait_max_ms 기다린다
        if tick.cfg.features.pause_wait and not in_pause(tick):
            if st.hold is None or st.hold.candidate_id != top.candidate_id:
                st.hold = Hold(candidate_id=top.candidate_id, since_ms=tick.t)
            if tick.t - st.hold.since_ms < pc.pause_wait_max_ms:
                top.status = CandidateStatus.WAITING
                top.reasons_against.append(Reason.WAITING_FOR_PAUSE)
                for c in eligible[1:]:
                    c.status = CandidateStatus.OUTRANKED
                return Selection(
                    Action.WAIT, None, [Reason.WAITING_FOR_PAUSE.value], top.candidate_id
                )
            top.reasons_for.append(Reason.PAUSE_TIMEOUT)

        st.hold = None
        top.status = CandidateStatus.SELECTED
        for c in eligible[1:]:
            c.status = CandidateStatus.OUTRANKED
        reasons = [top.issue.value] + [r.value for r in top.reasons_for]
        return Selection(Action.INTERVENE, top, reasons, top.candidate_id)


def in_pause(tick: Tick) -> bool:
    """발표자가 지금 말이 끊긴 틈인가. 판단할 신호가 없으면 기다릴 근거도 없으니 True."""
    pc = tick.cfg.policy
    voice = tick.req.current.voice
    speech = tick.req.current.speech
    has_signal = False
    if voice is not None and voice.audio_live:
        has_signal = True
        if voice.silence_ms >= pc.pause_silence_ms:
            return True
    if speech is not None and speech.utterance_end_ms is not None:
        has_signal = True
        if 0 <= tick.t - speech.utterance_end_ms <= pc.utterance_end_recent_ms:
            return True
    return not has_signal


RULE_POLICY = RulePolicy()
