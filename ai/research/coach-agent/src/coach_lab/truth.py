"""정답 — 시뮬레이터가 기록한 '발표자가 실제로 어땠는지'로 리뷰 근거의 정답을 만든다.

정답 리뷰 = 잡음 없는 실제 상태 × 코치와 **같은 판정 규칙**(coach.review.assess).
그래서 실험에서 코치 리뷰 근거와 정답이 다르면,
그 차이는 측정(잡음 · 창 지연 · 구간 처리)에서 온 것입니다.

정답이 지키는 원칙: 센서가 볼 수 없던 문제는 정답 리뷰도 말하지 않는다.
이상적인 리뷰 에이전트는 '대본을 봤을 것 같다'가 아니라
'믿을 수 있는 데이터로 봤다'만 말해야 하기 때문입니다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from coach.config import CoachConfig, load_config
from coach.evaluators.base import ramp
from coach.review import Agg, Assessment, Seg, assess
from coach.schemas import Memory, Mission, Plan
from coach.vocab import FeedbackType, Issue

from .simulator import RunResult

#: 정답 구간을 만드는 영역. TIME · CONTENT 는 장별 누적에서 정확히 계산되므로 구간 비교에서 뺀다
PROBLEM_TYPES: tuple[FeedbackType, ...] = (
    FeedbackType.GAZE,
    FeedbackType.SPEED,
    FeedbackType.VOLUME,
    FeedbackType.PAUSE,
    FeedbackType.FILLER,
)

_ISSUE_OF = {
    FeedbackType.GAZE: Issue.GAZE_ON_SCRIPT,
    FeedbackType.SPEED: Issue.PACE_FAST,
    FeedbackType.VOLUME: Issue.VOLUME_LOW,
    FeedbackType.PAUSE: Issue.LONG_SILENCE,
    FeedbackType.FILLER: Issue.FILLER_FREQUENT,
}


@dataclass
class TruthInterval:
    area: FeedbackType
    slide_number: int | None
    start_ms: int
    end_ms: int  # 배타
    observable: bool
    peak_severity: float
    burden_s: float

    @property
    def span_ms(self) -> int:
        return self.end_ms - self.start_ms


def _severity(ftype: FeedbackType, row: dict[str, Any], cfg: CoachConfig) -> float | None:
    """이 초에 그 영역 문제가 실제로 있었으면 심각도, 없으면 None. 기준은 코치 설정과 같다."""
    if ftype == FeedbackType.GAZE:
        r = row["script_ratio"]
        return (
            ramp(r, cfg.gaze.script_ratio, cfg.gaze.script_ratio_bad)
            if r >= cfg.gaze.script_ratio
            else None
        )
    if not row["speaking"]:
        return None
    if ftype == FeedbackType.SPEED:
        c = row["cpm"]
        return (
            ramp(c, cfg.speech.fast_cpm, cfg.speech.fast_cpm_bad)
            if c > cfg.speech.fast_cpm
            else None
        )
    if ftype == FeedbackType.VOLUME:
        d = row["voice_diff_db"]
        low = cfg.voice.low_relative_db
        return ramp(d, low, cfg.voice.low_relative_db_bad) if d < low else None
    if ftype == FeedbackType.FILLER:
        f = row["filler_per_min"]
        thr = cfg.speech.filler_threshold
        return ramp(f, thr, cfg.speech.filler_bad) if f >= thr else None
    return None


def _observable(ftype: FeedbackType, row: dict[str, Any]) -> bool:
    if ftype == FeedbackType.GAZE:
        return bool(row["gaze_ok"])
    if ftype in (FeedbackType.SPEED, FeedbackType.FILLER):
        return bool(row["stt_ok"])
    return bool(row["audio_live"])


def truth_intervals(
    run: RunResult, *, min_len_ms: int = 3_000, merge_gap_ms: int = 2_000
) -> list[TruthInterval]:
    cfg = run.config
    tick = run.scenario.tick_ms
    rows = run.presenter.truth
    out: list[TruthInterval] = []

    for ftype in PROBLEM_TYPES:
        runs: list[list[tuple[dict[str, Any], float]]] = []
        if ftype == FeedbackType.PAUSE:
            # 말을 멈춘 구간 전체. long_silence_ms 이상 이어졌을 때만 문제
            cur: list[tuple[dict[str, Any], float]] = []
            for row in rows:
                silent = not row["speaking"] and row["t_ms"] < (run.presenter.finished_at or 10**12)
                if silent:
                    cur.append((row, 0.75))
                elif cur:
                    runs.append(cur)
                    cur = []
            if cur:
                runs.append(cur)
            runs = [r for r in runs if len(r) * tick > cfg.voice.long_silence_ms]
        else:
            cur = []
            last_t = None
            for row in rows:
                sev = _severity(ftype, row, cfg)
                if sev is None:
                    continue
                gap = cur and last_t is not None and row["t_ms"] - last_t > merge_gap_ms
                new_slide = (
                    cur and ftype == FeedbackType.GAZE and row["slide"] != cur[-1][0]["slide"]
                )
                if gap or new_slide:
                    runs.append(cur)
                    cur = []
                cur.append((row, sev))
                last_t = row["t_ms"]
            if cur:
                runs.append(cur)

        for r in runs:
            start = r[0][0]["t_ms"]
            end = r[-1][0]["t_ms"] + tick
            if end - start < min_len_ms:
                continue
            seen = sum(1 for row, _ in r if _observable(ftype, row))
            peak = max(s for _, s in r)
            out.append(
                TruthInterval(
                    area=ftype,
                    slide_number=r[0][0]["slide"],
                    start_ms=start,
                    end_ms=end,
                    observable=seen * 2 > len(r),
                    peak_severity=peak,
                    burden_s=sum(s for _, s in r) * tick / 1000,
                )
            )
    return sorted(out, key=lambda i: (i.start_ms, i.area.value))


def truth_aggs(run: RunResult) -> tuple[dict[int, Agg], Agg]:
    cfg = run.config
    tick = run.scenario.tick_ms
    plan = Plan.model_validate(run.scenario.plan)
    targets = {s.slide_number: s for s in plan.slides}
    slides: dict[int, Agg] = {}
    take = Agg(slide_number=None)

    def agg_for(n: int | None) -> list[Agg]:
        if n is None:
            return [take]
        if n not in slides:
            sp = targets.get(n)
            slides[n] = Agg(
                slide_number=n,
                target_ms=sp.target_ms if sp else None,
                kw_required=list(sp.required_keywords) if sp else [],
            )
        return [slides[n], take]

    silence_run = 0
    for row in run.presenter.truth:
        silence_run = silence_run + tick if not row["speaking"] else 0
        for a in agg_for(row["slide"]):
            a.total_ms += tick
            if row["gaze_ok"]:
                a.gaze_valid_ms += tick
                a.gaze_script_ms += row["script_ratio"] * tick
            else:
                a.gaze_unusable_ms += tick
            if row["stt_ok"]:
                a.speech_ok_ms += tick
                if row["speaking"]:
                    a.cpm_ms += tick
                    a.cpm_weighted += row["cpm"] * tick
            if row["audio_live"]:
                a.audio_live_ms += tick
                if row["speaking"]:
                    a.speaking_ms += tick
                    a.db_ms += tick
                    a.db_weighted += row["voice_diff_db"] * tick
                if silence_run > cfg.voice.long_silence_ms:
                    a.long_silence_ms += tick

    for v in run.presenter.visits:
        n = v["slide"]
        dur = v.get("end_ms", run.end_ms) - v["start_ms"]
        for a in agg_for(n):
            a.visits += 1 if a is not take else 0
            a.duration_ms += dur
    take.visits = len(run.presenter.visits)

    spoken: dict[int | None, str] = {}
    for w in run.presenter.words:
        if w.filler:
            for a in agg_for(w.slide):
                a.filler_count += 1
        else:
            spoken[w.slide] = spoken.get(w.slide, "") + w.w
    for n, a in slides.items():
        text = spoken.get(n, "")
        a.kw_found = [k for k in a.kw_required if "".join(k.split()) in text]
        take.kw_required += [f"{n}:{k}" for k in a.kw_required]
        take.kw_found += [f"{n}:{k}" for k in a.kw_found]
    return slides, take


def truth_segments(intervals: list[TruthInterval]) -> list[Seg]:
    return [
        Seg(
            area=i.area,
            slide_number=i.slide_number,
            issue_types=[_ISSUE_OF[i.area]],
            start_ms=i.start_ms,
            end_ms=i.end_ms,
            onset_ms=i.start_ms,
            offset_ms=i.end_ms,
            peak_severity=i.peak_severity,
            # 정답 구간의 부담은 초마다의 실제 심각도 합 = 평균 심각도 × 길이
            mean_severity=i.burden_s * 1000 / i.span_ms if i.span_ms else i.peak_severity,
            reliable_ms=i.span_ms if i.observable else 0,
            unreliable_ms=0 if i.observable else i.span_ms,
        )
        for i in intervals
    ]


def truth_config(run: RunResult) -> CoachConfig:
    """정답 판정 설정 — 정답 구간은 이미 정확하므로 지연 보정 · 병합을 하지 않는다."""
    return load_config(
        **{
            **run.config.model_dump(mode="json"),
            "review": {
                **run.config.review.model_dump(mode="json"),
                "lag_compensation": False,
                "exclude_unreliable": True,
                "merge_gap_ms": 0,
            },
        }
    )


def truth_assessment(run: RunResult) -> tuple[Assessment, list[TruthInterval]]:
    intervals = truth_intervals(run)
    slides, take = truth_aggs(run)
    sc = run.scenario
    result = assess(
        slides,
        take,
        truth_segments(intervals),
        Plan.model_validate(sc.plan),
        [Mission.model_validate(m) for m in sc.missions],
        Memory.model_validate(sc.memory),
        truth_config(run),
    )
    return result, intervals


def truth_outcome(
    run: RunResult,
    issue: Issue,
    t_ms: int,
    check_ms: int,
    slide: int | None,
    keyword: str | None = None,
) -> bool | None:
    """개입 효과의 정답 — 코치와 같은 효과 규칙을 잡음 없는 실제 상태에 적용한다.

    판단할 수 없으면 None.
    """
    cfg = run.config
    rc = cfg.reflection
    rows = run.presenter.truth
    by_t = {r["t_ms"]: r for r in rows}
    before, after = by_t.get(t_ms), by_t.get(check_ms)
    if before is None or after is None:
        return None
    match issue:
        case Issue.GAZE_ON_SCRIPT:
            # 실제 값은 잡음이 없으니 여유폭 없이 탐지 기준 아래로 내려왔는지만 본다
            return after["script_ratio"] < cfg.gaze.script_ratio
        case Issue.PACE_FAST:
            if not after["speaking"]:
                return None
            a, b = after["cpm"], before["cpm"]
            return a <= cfg.speech.fast_cpm or a <= b * (1 - rc.cpm_drop_ratio)
        case Issue.VOLUME_LOW:
            if not after["speaking"]:
                return None
            a, b = after["voice_diff_db"], before["voice_diff_db"]
            return a >= cfg.voice.low_relative_db or a >= b + rc.volume_gain_db
        case Issue.FILLER_FREQUENT:
            a, b = after["filler_per_min"], before["filler_per_min"]
            return a <= b * (1 - rc.filler_drop_ratio)
        case Issue.LONG_SILENCE:
            return any(r["speaking"] for r in rows if t_ms < r["t_ms"] <= check_ms)
        case Issue.SLIDE_OVER:
            return after["slide"] != slide
        case Issue.KEYWORD_MISSING:
            if keyword is None:
                return None
            said = "".join(
                w.w for w in run.presenter.words if t_ms <= w.start_ms <= check_ms and not w.filler
            )
            return "".join(keyword.split()) in said
    return None
