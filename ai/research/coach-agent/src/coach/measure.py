"""판정 라운드(JudgeRun)를 코치 규칙이 읽는 Tick 으로 바꾼다.

후보 · 적격성 · 우선순위 · 되돌아보기는 Tick 의 metrics · detections 를 읽는다. 판정 모듈이 준
결과를 그 모양으로 옮기는 일만 여기서 한다 — 판정 기준은 모듈이 갖고 코치는 값을 다시 계산하지
않는다. 개입 규칙이 판정 결과를 직접 읽게 되면(#150 의 개입 규칙 PR) 이 어댑터를 줄인다.
"""

from __future__ import annotations

import logging

from .config import CoachConfig
from .judges import JudgeRun
from .schemas import CoachRequest, JudgmentResult
from .state import CoachState
from .tick import Detection, Tick
from .vocab import FeedbackType, Issue

log = logging.getLogger(__name__)

#: 문제 → 악화 판단에 쓰는 대표 지표 (tick.metrics 의 키). 없으면 악화를 보지 않는다
ISSUE_METRIC: dict[Issue, str | None] = {
    Issue.GAZE_ON_SCRIPT: "script_ratio",
    Issue.GAZE_AWAY: None,
    Issue.GAZE_LOW_EYE_CONTACT: None,
    Issue.GAZE_ON_SCREEN: None,
    Issue.GAZE_UNMEASURABLE: None,
    Issue.PACE_FAST: "cpm",
    Issue.PACE_SLOW: None,
    Issue.VOLUME_LOW: "voice_diff_db",
    Issue.FILLER_FREQUENT: "recent_filler_count",
    Issue.LONG_SILENCE: None,
    Issue.BEHIND_SCHEDULE: "required_ratio",
    Issue.AHEAD_OF_SCHEDULE: None,
    Issue.SLIDE_OVER: None,
    Issue.FINAL_MINUTE: None,
    Issue.TIME_OVER: None,
}

#: STT 단어로 재는 영역 — STT 를 믿을 수 없으면 지표 · 문제를 쓰지 않는다
_STT_AREAS = (FeedbackType.SPEED, FeedbackType.FILLER)

#: 상태 표시에 담을 영역
INDICATOR_AREAS = (
    FeedbackType.GAZE,
    FeedbackType.SPEED,
    FeedbackType.VOLUME,
    FeedbackType.PAUSE,
    FeedbackType.FILLER,
    FeedbackType.TIME,
)


def build_tick(req: CoachRequest, cfg: CoachConfig, state: CoachState, run: JudgeRun) -> Tick:
    inputs = req.inputs
    if inputs.slide is not None:
        slide, slide_start = inputs.slide.number, inputs.slide.started_ms
    elif state.slide_log:
        # 장 정보가 이번 요청에 없으면 마지막으로 알던 장 (judges.run 이 시간 판정에 넘긴 장과 같다)
        slide, slide_start = state.slide_log[-1]
    else:
        slide, slide_start = None, None
    state.slide_number = slide

    if state.last_t_ms is None:
        dt = cfg.policy.default_tick_ms
    else:
        dt = min(cfg.policy.max_tick_gap_ms, req.t_ms - state.last_t_ms)

    # STT 를 믿을 수 있는가: 상태가 ok 이고 오디오가 살아 있고(judges.run), 군더더기 · 말 속도
    # 판정이 이번에 성공했다. 아니면 말 속도 · 군더더기로 말을 걸지 않고 장별 누적에서도 뺀다
    stt_ok = run.stt_ok and not ({"filler", "pace"} & run.failed)
    tick = Tick(
        req=req,
        cfg=cfg,
        state=state,
        t=req.t_ms,
        slide_number=slide,
        slide_start_ms=slide_start,
        stt_ok=stt_ok,
        dt_ms=dt,
        criteria=run.criteria,
        summarize=run.summarize,
    )

    # 지표: 모든 결과의 metrics 를 한 dict 로 (이름은 모듈 사이에서 겹치지 않는다). STT 를 믿을 수
    # 없으면 말 속도 · 군더더기 지표는 비운다 — 효과 판정 · 장별 누적에 쓰지 않게
    for result in run.results.values():
        if not stt_ok and result.area in _STT_AREAS:
            tick.metrics.update(dict.fromkeys(result.metrics))
        else:
            tick.metrics.update(result.metrics)

    # 말하는 중 · 기준 대비 음량: 가장 최근 1초 기록
    latest = max(inputs.voice_records, key=lambda r: r.t_ms, default=None)
    if latest is not None and latest.audio_live:
        tick.speaking = latest.silence_ms < cfg.policy.pause_silence_ms
        if tick.speaking and latest.level_db is not None and state.base_level_db is not None:
            tick.voice_diff_db = round(latest.level_db - state.base_level_db, 2)

    filler = run.results.get(FeedbackType.FILLER)
    if filler is not None and stt_ok:
        tick.filler_new = int(sum(p.values.get("filler_count", 0) for p in filler.tally))
        # 효과를 잴 때 구간별로 다시 세려고 말한 시각과 함께 남긴다
        state.filler_times.extend(
            [p.t_ms, int(p.values["filler_count"])]
            for p in filler.tally
            if p.values.get("filler_count", 0) > 0
        )
        if filler.measurable:
            _note_filler_ok(state, filler)
    _prune_filler_times(tick)

    for result in run.results.values():
        for issue in result.issues:
            if issue.issue_type not in cfg.issues:
                log.debug("규칙이 없는 문제는 건너뛴다: %s", issue.issue_type)
                continue
            tick.detections.append(_detection(tick, result, issue))
    return tick


def _note_filler_ok(state: CoachState, filler: JudgmentResult) -> None:
    """군더더기 수를 믿을 수 있던 시간(이번 결과의 시간 몫)을 합쳐 남긴다."""
    for piece in filler.tally:
        span = piece.values.get("total_ms")
        if not span:
            continue
        lo, hi = piece.t_ms, piece.t_ms + int(span)
        last = state.filler_ok[-1] if state.filler_ok else None
        if last is not None and lo <= last[1]:
            last[1] = max(last[1], hi)
        else:
            state.filler_ok.append([lo, hi])


def _prune_filler_times(tick: Tick) -> None:
    """군더더기 효과를 재는 데 필요한 만큼만 남긴다: 재기로 한 개입의 앞 구간부터, 그리고 지금
    개입해도 앞 구간이 되는 최근 구간."""
    state = tick.state
    rule = tick.cfg.issues.get(Issue.FILLER_FREQUENT)
    window = rule.outcome_delay_ms if rule is not None else None
    if window is None:
        state.filler_times = []
        state.filler_ok = []
        return
    horizon = min(
        [tick.t - window, *(p.t_ms - window for p in state.pending if p.metric == "filler_count")]
    )
    state.filler_times = [e for e in state.filler_times if e[0] > horizon]
    state.filler_ok = [s for s in state.filler_ok if s[1] > horizon]


def indicators(run: JudgeRun) -> dict[str, str]:
    return {a.value: run.results[a].state for a in INDICATOR_AREAS if a in run.results}


def _detection(tick: Tick, result: JudgmentResult, issue) -> Detection:
    ev = issue.evidence
    params: dict = {}
    min_step = 0
    if issue.issue_type == Issue.BEHIND_SCHEDULE:
        params = {
            "remaining_slides": ev.get("remaining_slides"),
            "remaining_time_ms": ev.get("remaining_ms"),
        }
        min_step = 1 if ev.get("speed_limit_exceeded") else 0
    elif issue.issue_type == Issue.SLIDE_OVER:
        params = {"over_time_ms": ev.get("over_ms")}
    return Detection(
        issue_type=issue.issue_type,
        severity=issue.severity,
        confidence=issue.confidence,
        evidence=dict(ev),
        sensor_ok=result.measurable
        and issue.actionable
        and (tick.stt_ok or result.area not in _STT_AREAS),
        slide_number=tick.slide_number,
        metric=ISSUE_METRIC.get(issue.issue_type),
        params=params,
        min_step=min_step,
    )
