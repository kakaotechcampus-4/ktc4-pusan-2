"""코치의 유일한 진입점.

    decide(request, judges)   1초마다. 지금 상황 + coach_state → 행동 + 이벤트 + 새 coach_state
    finalize(request, judges)
                      Take 종료 때 한 번. 마지막 창을 판정하고 열린 것을 닫아 Take 결과를 만든다

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
from . import eligibility, episodes, measure, priority, reflection, slides, take_lists, tally
from . import judges as judges_mod
from .candidates import Candidate
from .config import DEFAULT_CONFIG, CoachConfig
from .events import EventSink
from .judges import JudgeRun, Judges
from .policy import RULE_POLICY, Policy, rank_key
from .renderer import render
from .schemas import (
    AreaCriteria,
    AreaResult,
    CoachRequest,
    CoachResponse,
    Feedback,
    FinalizeRequest,
    FinalizeResponse,
    InterventionEvent,
    IssueCriteria,
    Meta,
    OutcomeEvent,
    SlideResult,
    SuppressedEvent,
    TakeResult,
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
from .version import FEATURE_VERSION
from .vocab import ISSUE_TYPE, Action, CandidateStatus, FeedbackType, Issue, Outcome, Reason

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
    tick, run = _round(req, judges, cfg, state, sink)

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
        feedback=feedback,
        indicators=measure.indicators(run),
        events=sink.events,
    )


def decide_safe(
    request: CoachRequest | dict[str, Any],
    judges: Judges,
    config: CoachConfig | None = None,
    policy: Policy | None = None,
) -> CoachResponse:
    """API 가 부르는 판. 코치 안에서 예외가 나면 WAIT(INTERNAL_ERROR) 를 돌려준다.

    coach_state 는 받은 상태에서 이번 시간을 잴 수 없던 것으로 넘긴 상태다(모든 커서가 이번 창
    끝으로 가고, 같은 요청이 다시 오면 STALE_TICK). 순수 코드라 같은 요청을 되돌려 다시 하면
    같은 예외에서 빠져나오지 못하기 때문이다. 버전이 다르거나 깨진 state 였으면 새 state 에서
    시작하고 STATE_RESET 도 남긴다.

    요청 형식 오류는 그대로 올린다 — API 가 422 로 바꾸고, BE 는 그 1초를 건너뛴다.
    """
    cfg = config or DEFAULT_CONFIG
    req = request if isinstance(request, CoachRequest) else CoachRequest.model_validate(request)
    try:
        return decide(req, judges, cfg, policy)
    except Exception:  # noqa: BLE001 — 실시간 경로는 어떤 예외로도 발표를 방해하면 안 된다
        log.exception("coach decide failed take_id=%s t_ms=%s", req.take_id, req.t_ms)
        state, reset = load_state(req.coach_state)
        reasons = [Reason.INTERNAL_ERROR.value]
        if reset:
            reasons.append(Reason.STATE_RESET.value)
        try:
            judges_mod.skip(req, state)
            state.last_t_ms = req.t_ms
        except Exception:  # noqa: BLE001 — 넘기지 못해도 발표를 방해하지 않는다
            log.exception("coach skip failed take_id=%s t_ms=%s", req.take_id, req.t_ms)
            # 일부만 반영된 state 를 버리고 받은 상태 그대로 돌려준다
            state, _ = load_state(req.coach_state)
            if req.coach_state is not None and not reset:
                return _response(req, cfg, Action.WAIT, reasons, state, coach_state=req.coach_state)
        return _response(req, cfg, Action.WAIT, reasons, state)


class ReplayRequired(Exception):
    """지금까지 센 구간이 Take 를 덮지 못해 합계로 Take 결과를 만들 수 없다.

    나중에 API 가 409 REPLAY_REQUIRED 로 바꾼다. missing 은 빠진 구간 [시작, 끝) 목록이고,
    coach_state 가 없거나 새로 시작했으면 Take 전체다.
    """

    def __init__(self, message: str, missing: list[tuple[int, int]]) -> None:
        super().__init__(message)
        self.missing = missing


#: 영역 순서 · 영역 → 판정 기준과 지표를 내는 모듈
_AREA_MODULE: dict[FeedbackType, str] = {
    FeedbackType.GAZE: "gaze",
    FeedbackType.SPEED: "pace",
    FeedbackType.VOLUME: "volume",
    FeedbackType.PAUSE: "volume",
    FeedbackType.FILLER: "filler",
    FeedbackType.TIME: "timing",
}


def finalize(
    request: FinalizeRequest | dict[str, Any],
    judges: Judges,
    config: CoachConfig | None = None,
) -> FinalizeResponse:
    """Take 종료. 마지막 창을 판정하고 열린 것을 닫아 Take 결과를 만든다.

    ① 마지막 창: decide 와 같은 판정 · 누적을 한 번 더 한다(말은 걸지 않는다). Take 끝을 문장 끝
       신호로 넣어 뒤 간격을 기다리던 단어까지 판정한다. 이미 그 시각을 처리했으면 건너뛴다.
    ② 닫기: 재지 못한 효과는 NOT_MEASURED, 열린 문제 구간은 TAKE_END, 마지막 장은 SLIDE.
    ③ 센 구간이 [0, t_ms) 를 덮지 못하면 ReplayRequired.
    ④ 합계(Take · 장 번호별)를 영역 모듈의 summarize 에 넣어 areas 를 만들고, 판정 기준을 남긴다.
    ⑤ 받은 이벤트 + 이번 이벤트(event_id 로 중복을 거른 것)로 문제 구간 · 개입 · 포기를 만든다.
    """
    cfg = config or DEFAULT_CONFIG
    req = (
        request if isinstance(request, FinalizeRequest) else FinalizeRequest.model_validate(request)
    )
    t = req.t_ms
    state, reset = load_state(req.coach_state)
    if req.coach_state is None or reset:
        why = "coach_state 가 없다" if req.coach_state is None else "coach_state 를 새로 시작했다"
        raise ReplayRequired(f"{why}: 센 구간이 없다. 빠진 구간 [0, {t})", [(0, t)])

    sink = EventSink(state)
    run: JudgeRun | None = None
    if state.last_t_ms is None or t > state.last_t_ms:
        state, sink, run = _last_window(req, judges, cfg, state, sink)

    for p in state.pending:
        sink.emit(
            OutcomeEvent,
            t_ms=t,
            intervention_id=p.intervention_id,
            outcome=Outcome.NOT_MEASURED,
            metric=p.metric,
            before=p.before,
        )
    state.pending = []
    for key in list(state.episodes):
        episodes.close(state, key, t, sink, cfg)
    slides.finish(state, t, sink)
    tally.extend_slide_span(state, t)

    missing = _missing_spans(state.covered, t)
    if missing:
        spans = ", ".join(f"[{lo}, {hi})" for lo, hi in missing)
        raise ReplayRequired(f"센 구간이 Take 를 덮지 못했다. 빠진 구간 {spans}", missing)

    criteria = run.criteria if run is not None else judges_mod.criteria_of(judges, cfg)
    # 받은 이벤트와 이번에 만든 이벤트를 event_id 로 거르며 합쳐 목록을 만든다
    all_events = take_lists.merge_events(req.events, sink.events)
    result = TakeResult(
        take_id=req.take_id,
        duration_ms=t,
        script_mode=req.script_mode,
        replayed=False,
        criteria_changed=any(
            v != state.latest_criteria_versions.get(name)
            for name, v in state.criteria_versions.items()
        ),
        areas=_areas(req, judges, cfg, state),
        problem_segments=take_lists.problem_segments(
            all_events, cfg.take_result, criteria, t, state.slide_spans, cfg.policy.default_tick_ms
        ),
        interventions=take_lists.interventions(all_events),
        gave_up=take_lists.gave_up(all_events),
        criteria=_criteria_snapshot(state, criteria),
    )
    return FinalizeResponse(take_result=result, events=sink.events, meta=_meta(cfg, state))


# ── 내부 ──────────────────────────────────────────────────────────────────


def _round(
    req: CoachRequest, judges: Judges, cfg: CoachConfig, state: CoachState, sink: EventSink
) -> tuple[Tick, JudgeRun]:
    """판정 라운드 · 측정 정리 · 되돌아보기 · 문제 구간(① ②). decide 와 finalize 가 같이 쓴다."""
    run = judges_mod.run(req, state, judges, cfg)
    tick = measure.build_tick(req, cfg, state, run)
    slides.emit_ready(state, req.t_ms, sink)
    reflection.resolve(tick, sink)
    reflection.prune_praise(tick)
    episodes.observe(tick, sink)
    return tick, run


def _last_window(
    req: FinalizeRequest, judges: Judges, cfg: CoachConfig, state: CoachState, sink: EventSink
) -> tuple[CoachState, EventSink, JudgeRun | None]:
    """마지막 창을 decide 처럼 판정한다(후보 · 개입 없이). 예외가 나면 decide_safe 처럼 그 시간을
    잴 수 없던 것으로 넘긴다. 넘기지도 못하면 받은 상태 그대로 둔다(센 구간이 모자라 409).
    """
    t = req.t_ms
    ends = req.inputs.utterance_ends
    # Take 끝을 문장 끝으로 본다 — 뒤 간격을 기다리던 단어까지 판정한다
    inputs = req.inputs.model_copy(update={"utterance_ends": ends if t in ends else [*ends, t]})
    creq = CoachRequest(
        take_id=req.take_id,
        t_ms=t,
        mode=req.mode,
        plan=req.plan,
        missions=req.missions,
        recurring_issues=req.recurring_issues,
        coaching_plan=req.coaching_plan,
        script_used=req.script_used,
        inputs=inputs,
        calibration=req.calibration,
    )
    try:
        _, run = _round(creq, judges, cfg, state, sink)
        state.last_t_ms = t
        return state, sink, run
    except Exception:  # noqa: BLE001 — 마지막 창이 깨져도 이미 센 합계로 Take 결과를 낸다
        log.exception("coach finalize window failed take_id=%s t_ms=%s", req.take_id, t)
    state, _ = load_state(req.coach_state)
    sink = EventSink(state)
    try:
        judges_mod.skip(creq, state)
        state.last_t_ms = t
    except Exception:  # noqa: BLE001
        log.exception("coach finalize skip failed take_id=%s t_ms=%s", req.take_id, t)
        state, _ = load_state(req.coach_state)
        sink = EventSink(state)
    return state, sink, None


def _missing_spans(covered: list[list[int]], t_ms: int) -> list[tuple[int, int]]:
    """센 구간이 덮지 못한 [0, t_ms) 의 조각."""
    missing: list[tuple[int, int]] = []
    pos = 0
    for lo, hi in sorted(covered):
        if pos >= t_ms:
            break
        if lo > pos:
            missing.append((pos, min(lo, t_ms)))
        pos = max(pos, hi)
    if pos < t_ms:
        missing.append((pos, t_ms))
    return missing


def _areas(
    req: FinalizeRequest, judges: Judges, cfg: CoachConfig, state: CoachState
) -> dict[str, AreaResult]:
    """Take 합계와 장 번호별 합계를 영역 모듈의 summarize 에 넣어 영역별 값을 만든다."""
    floor = cfg.take_result.min_measured_ratio
    targets = {s.slide_number: s.target_ms for s in req.plan.slides}
    numbers = sorted(int(k) for k in state.slide_totals)
    out: dict[str, AreaResult] = {}
    for area in _AREA_MODULE:
        take, ratio = _summary(judges, area, state.totals, cfg)
        if ratio is None or ratio < floor:
            out[area.value] = AreaResult(
                measured_ratio=ratio, unmeasured_reason="LOW_MEASURED_RATIO"
            )
            continue
        if area == FeedbackType.VOLUME:
            take = {**(take or {}), "base_level_source": state.base_level_source}
        slide_results = []
        for n in numbers:
            metrics, slide_ratio = _summary(judges, area, state.slide_totals[str(n)], cfg)
            if area == FeedbackType.TIME and metrics is not None:
                metrics = {
                    ("slide_" + k if k == "duration_ms" else k): v for k, v in metrics.items()
                }
            span = state.slide_spans.get(str(n))
            slide_results.append(
                SlideResult(
                    slide_number=n,
                    start_ms=span[0] if span else None,
                    end_ms=span[1] if span else None,
                    target_ms=targets.get(n),
                    measured_ratio=slide_ratio,
                    metrics=metrics if slide_ratio is not None and slide_ratio >= floor else None,
                )
            )
        out[area.value] = AreaResult(measured_ratio=ratio, take=take, slides=slide_results)
    return out


def _summary(
    judges: Judges, area: FeedbackType, totals: dict[str, dict[str, float]], cfg: CoachConfig
) -> tuple[dict[str, Any] | None, float | None]:
    """합계 → (measured_ratio 를 뺀 그 영역 지표, measured_ratio). 못 읽으면 (None, None)."""
    summary = judges_mod.summarize(judges, area, totals, cfg)
    values = summary.get(area.value) if summary is not None else None
    if not isinstance(values, dict):
        return None, None
    rest = {k: v for k, v in values.items() if k != "measured_ratio"}
    return rest, values.get("measured_ratio")


def _criteria_snapshot(
    state: CoachState, criteria: dict[str, dict[str, IssueCriteria]]
) -> dict[str, AreaCriteria]:
    """영역별 판정 기준. 소리 크기 모듈의 문제는 원인 영역(VOLUME · PAUSE)으로 나눈다."""
    out: dict[str, AreaCriteria] = {}
    for area, module in _AREA_MODULE.items():
        issues: dict[str, IssueCriteria] = {}
        for key, value in criteria.get(module, {}).items():
            try:
                issue = Issue(key)
            except ValueError:
                continue
            if ISSUE_TYPE.get(issue) == area:
                issues[issue.value] = IssueCriteria.model_validate(
                    value if isinstance(value, dict) else value.model_dump()
                )
        out[area.value] = AreaCriteria(
            criteria_version=state.latest_criteria_versions.get(module), issues=issues
        )
    return out


def _meta(cfg: CoachConfig, state: CoachState) -> Meta:
    # 기능 모듈의 기준 버전은 이번 라운드까지 state 가 아는 것, coach 는 기능 버전 + 설정 해시
    versions = {**state.latest_criteria_versions, "coach": f"{FEATURE_VERSION}+{cfg.config_hash()}"}
    return Meta(criteria_versions=versions)


def _intervene(tick: Tick, c: Candidate, reason_codes: list[str], sink: EventSink) -> Feedback:
    st = tick.state
    message = render(tick.cfg, c.instruction, c.variant, c.params)
    confidence = round(min(1.0, max(0.0, c.confidence)), 3)
    event = sink.emit(
        InterventionEvent,
        t_ms=tick.t,
        issue_type=c.issue_type,
        area=c.area,
        instruction=c.instruction,
        message=message,
        priority=c.priority,
        confidence=confidence,
        reason_codes=reason_codes,
        slide_number=c.slide_number,
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
        last = st.suppress_log.get(c.episode_key)
        if last is not None and tick.t - last < gap:
            continue
        st.suppress_log[c.episode_key] = tick.t
        sink.emit(
            SuppressedEvent,
            t_ms=tick.t,
            issue_type=c.issue_type,
            area=c.area,
            slide_number=c.slide_number,
            instruction=c.instruction,
            status=c.status,
            priority=c.priority,
            reasons=[r.value for r in c.reasons_against],
        )


def _append_history(tick: Tick) -> None:
    st = tick.state
    sample = HistorySample(
        t_ms=tick.t,
        script_ratio=tick.metrics.get("script_ratio"),
        script_ratio_short=tick.metrics.get("script_ratio_short"),
        away_ratio_short=tick.metrics.get("away_ratio_short"),
        audience_ratio_short=tick.metrics.get("audience_ratio_short"),
        cpm=tick.metrics.get("cpm"),
        cpm_short=tick.metrics.get("cpm_short"),
        voice_diff_db=tick.voice_diff_db,
        speaking=tick.speaking,
        required_ratio=tick.metrics.get("required_ratio"),
        recent_filler_count=tick.metrics.get("recent_filler_count"),
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
    feedback: Feedback | None = None,
    indicators: dict[str, str] | None = None,
    events: list[Any] | None = None,
    coach_state: dict[str, Any] | None = None,
) -> CoachResponse:
    return CoachResponse(
        action=action,
        feedback=feedback,
        indicators=indicators or {},
        reason_codes=reasons,
        events=events or [],
        coach_state=dump_state(state) if coach_state is None else coach_state,
        meta=_meta(cfg, state),
    )


__all__ = ["ReplayRequired", "decide", "decide_safe", "finalize", "initial_state"]
