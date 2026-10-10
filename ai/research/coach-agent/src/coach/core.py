"""코치의 유일한 진입점.

    decide(request, judges)   1초마다. 지금 상황 + coach_state → 행동 + 이벤트 + 새 coach_state
    finalize(request) Take 종료 때 한 번. 열린 문제 구간과 재지 못한 효과를 닫는다

순서:
    ① 판정 라운드 — 판정 모듈(judges.run)이 센 합계와 1초 판정 결과
    ② 측정 정리   — 판정 결과 → Tick (지표 · 문제 Detection)
       되돌아보기 — 잴 때가 된 개입의 효과 판정 → 전략 수정
       문제 구간  — 열기 · 이어가기 · 닫기
    ③ 후보 생성 → ④ 적격성 필터 → ⑤ 우선순위 → ⑥ 행동 선택
    ⑦ 문구 렌더링 → ⑧ 이벤트 · 기록 · coach_state

이 함수는 시계 · 파일 · 네트워크를 쓰지 않습니다. 시간은 요청의 t_ms 뿐이라서
같은 요청에는 언제나 같은 응답이 나오고, research 의 재생 결과가 배포 결과와 같습니다.
"""

from __future__ import annotations

import logging
from typing import Any

from . import candidates as candidates_mod
from . import eligibility, episodes, measure, priority, reflection, slides
from . import judges as judges_mod
from .candidates import Candidate
from .config import DEFAULT_CONFIG, CoachConfig
from .events import EventSink
from .judges import Judges
from .policy import RULE_POLICY, Policy, rank_key
from .renderer import render
from .schemas import (
    CandidateOut,
    CoachRequest,
    CoachResponse,
    Feedback,
    FinalizeRequest,
    FinalizeResponse,
    InterventionEvent,
    OutcomeEvent,
    SuppressedEvent,
)
from .state import (
    CoachState,
    HistorySample,
    StrategyState,
    dump_state,
    initial_state,
    load_state,
)
from .tick import Tick
from .version import POLICY_VERSION
from .vocab import Action, CandidateStatus, Outcome, Reason

log = logging.getLogger(__name__)


def decide(
    request: CoachRequest | dict[str, Any],
    judges: Judges,
    config: CoachConfig | None = None,
    policy: Policy | None = None,
) -> CoachResponse:
    cfg = config or DEFAULT_CONFIG
    req = request if isinstance(request, CoachRequest) else CoachRequest.model_validate(request)
    state, reset = load_state(req.coach_state)

    # 이미 처리한 시각이 다시 오면(재시도 · 순서 뒤바뀜) 아무것도 바꾸지 않는다
    if state.last_t_ms is not None and req.t_ms <= state.last_t_ms:
        return _response(req, cfg, Action.WAIT, [Reason.STALE_TICK.value], state)

    sink = EventSink(state)
    run = judges_mod.run(req, state, judges, cfg)
    tick = measure.build_tick(req, cfg, state, run)
    slides.switch(tick, sink)
    slides.accumulate(tick)
    reflection.resolve(tick, sink)
    reflection.prune_praise(tick)
    episodes.observe(tick, sink)

    cands = candidates_mod.build(tick)
    eligibility.apply(tick, cands)
    for c in cands:
        priority.score(tick, c)
    selection = (policy or RULE_POLICY).select(tick, cands)

    feedback: Feedback | None = None
    if selection.action == Action.INTERVENE and selection.selected is not None:
        feedback = _intervene(tick, selection.selected, selection.reason_codes, sink)

    episodes.note_candidates(tick, cands)
    _log_suppressed(tick, cands, sink)
    _append_history(tick)
    state.last_t_ms = req.t_ms

    reasons = list(selection.reason_codes)
    if reset:
        reasons.append(Reason.STATE_RESET.value)
    return _response(
        req,
        cfg,
        selection.action,
        reasons,
        state,
        candidate_id=selection.candidate_id,
        feedback=feedback,
        candidates=cands,
        indicators=measure.indicators(run),
        events=sink.events,
    )


def decide_safe(
    request: CoachRequest | dict[str, Any],
    judges: Judges,
    config: CoachConfig | None = None,
    policy: Policy | None = None,
) -> CoachResponse:
    """API 가 부르는 판. 코치 안에서 예외가 나면 WAIT 와 이전 coach_state 를 돌려준다
    (버전이 다르거나 깨진 state 였으면 새 state).

    요청 형식 오류는 그대로 올린다 — API 가 422 로 바꾸고, BE 는 그 1초를 건너뛴다.
    """
    cfg = config or DEFAULT_CONFIG
    req = request if isinstance(request, CoachRequest) else CoachRequest.model_validate(request)
    try:
        return decide(req, judges, cfg, policy)
    except Exception:  # noqa: BLE001 — 실시간 경로는 어떤 예외로도 발표를 방해하면 안 된다
        log.exception("coach decide failed take_id=%s t_ms=%s", req.take_id, req.t_ms)
        state, reset = load_state(req.coach_state)
        kept = req.coach_state is not None and not reset
        return CoachResponse(
            policy_version=POLICY_VERSION,
            config_hash=cfg.config_hash(),
            take_id=req.take_id,
            t_ms=req.t_ms,
            action=Action.WAIT,
            reason_codes=[Reason.INTERNAL_ERROR.value],
            coach_state=req.coach_state if kept else dump_state(state),
        )


def finalize(
    request: FinalizeRequest | dict[str, Any], config: CoachConfig | None = None
) -> FinalizeResponse:
    """Take 종료. 열린 문제 구간을 TAKE_END 로 닫는다.

    아직 재지 못한 개입 효과는 NOT_MEASURED 로 남긴다.
    """
    cfg = config or DEFAULT_CONFIG
    req = (
        request if isinstance(request, FinalizeRequest) else FinalizeRequest.model_validate(request)
    )
    state, _ = load_state(req.coach_state)
    sink = EventSink(state)
    for p in state.pending:
        sink.emit(
            OutcomeEvent,
            t_ms=req.t_ms,
            intervention_id=p.intervention_id,
            candidate_id=p.candidate_id,
            issue_type=p.issue_type,
            area=p.area,
            instruction=p.instruction,
            slide_number=p.slide_number,
            outcome=Outcome.NOT_MEASURED,
            metric=p.metric,
            before=p.before,
        )
    state.pending = []
    for key in list(state.episodes):
        episodes.close(state, key, req.t_ms, "TAKE_END", sink, cfg)
    slides.close(state, req.t_ms, sink)
    return FinalizeResponse(policy_version=POLICY_VERSION, take_id=req.take_id, events=sink.events)


# ── 내부 ──────────────────────────────────────────────────────────────────


def _intervene(tick: Tick, c: Candidate, reason_codes: list[str], sink: EventSink) -> Feedback:
    st = tick.state
    message = render(tick.cfg, c.instruction, c.variant, c.params)
    confidence = round(min(1.0, max(0.0, c.confidence)), 3)
    event = sink.emit(
        InterventionEvent,
        t_ms=tick.t,
        candidate_id=c.candidate_id,
        issue_type=c.issue_type,
        area=c.area,
        instruction=c.instruction,
        variant=c.variant,
        message=message,
        priority=c.priority,
        confidence=confidence,
        reason_codes=reason_codes,
        slide_number=c.slide_number,
        evidence=c.evidence,
    )
    st.last_fired_ms = tick.t
    st.last_by_instruction[c.instruction.value] = tick.t
    st.fires_by_issue[c.issue_type.value] = st.fires_by_issue.get(c.issue_type.value, 0) + 1
    st.interventions += 1

    if c.praise is None:
        strat = st.strategy.setdefault(c.strategy_key, StrategyState())
        strat.fires += 1
        episodes.note_intervention(tick, c, event.event_id)
        reflection.register(tick, c, event.event_id)
    else:
        st.praise = [p for p in st.praise if p.intervention_id != c.praise.intervention_id]

    return Feedback(
        area=c.area,
        instruction=c.instruction,
        message=message,
        priority=c.priority,
        confidence=confidence,
        evidence=c.evidence,
    )


def _log_suppressed(tick: Tick, cands: list[Candidate], sink: EventSink) -> None:
    """참은 기록. 같은 문제는 suppress_log_gap_ms 에 한 번만 남긴다.

    침묵처럼 매초 걸리는 문제가 기록을 덮어 다른 것이 묻히지 않게 하려는 것이다.
    """
    gap = tick.cfg.policy.suppress_log_gap_ms
    st = tick.state
    for c in sorted(cands, key=rank_key):
        if c.status not in (CandidateStatus.WAITING, CandidateStatus.IGNORED):
            continue
        last = st.suppress_log.get(c.strategy_key)
        if last is not None and tick.t - last < gap:
            continue
        st.suppress_log[c.strategy_key] = tick.t
        sink.emit(
            SuppressedEvent,
            t_ms=tick.t,
            candidate_id=c.candidate_id,
            issue_type=c.issue_type,
            area=c.area,
            instruction=c.instruction,
            status=c.status,
            priority=c.priority,
            reasons=[r.value for r in c.reasons_against],
        )


def _append_history(tick: Tick) -> None:
    st = tick.state
    sample = HistorySample(
        t_ms=tick.t,
        slide_number=tick.slide_number,
        script_ratio=tick.metrics.get("script_ratio"),
        cpm=tick.metrics.get("cpm"),
        cpm_short=tick.metrics.get("cpm_short"),
        voice_diff_db=tick.voice_diff_db,
        speaking=tick.speaking,
        filler_new=tick.filler_new,
        required_ratio=tick.metrics.get("required_ratio"),
    )
    since = tick.t - tick.cfg.policy.history_ms
    st.history = [s for s in st.history if s.t_ms > since] + [sample]


def _response(
    req: CoachRequest,
    cfg: CoachConfig,
    action: Action,
    reasons: list[str],
    state: CoachState,
    *,
    candidate_id: str | None = None,
    feedback: Feedback | None = None,
    candidates: list[Candidate] | None = None,
    indicators: dict[str, str] | None = None,
    events: list[Any] | None = None,
) -> CoachResponse:
    outs = []
    for c in sorted(candidates or [], key=rank_key):
        status = c.status or CandidateStatus.IGNORED
        shown = c.reasons_for if status == CandidateStatus.SELECTED else c.reasons_against
        outs.append(
            CandidateOut(
                candidate_id=c.candidate_id,
                issue_type=c.issue_type,
                area=c.area,
                instruction=c.instruction,
                priority=c.priority,
                confidence=round(min(1.0, max(0.0, c.confidence)), 3),
                status=status,
                reasons=[r.value for r in shown],
            )
        )
    return CoachResponse(
        policy_version=POLICY_VERSION,
        config_hash=cfg.config_hash(),
        take_id=req.take_id,
        t_ms=req.t_ms,
        action=action,
        candidate_id=candidate_id,
        reason_codes=reasons,
        feedback=feedback,
        candidates=outs,
        indicators=indicators or {},
        events=events or [],
        coach_state=dump_state(state),
    )


__all__ = ["decide", "decide_safe", "finalize", "initial_state"]
