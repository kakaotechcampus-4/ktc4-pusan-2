"""시선 판정 — 연구용 대역.

지금 코치 평가기(`coach.evaluators.gaze`)의 규칙 · 기준값을 #153 계약 모양으로 옮긴 것이다.
기능 모듈이 나오면 이 대역 대신 그 모듈을 쓴다. 숫자는 지금 평가기와 같게 두고 출력 모양만 바꿨다.

상태를 갖지 않는다. 지금 평가기가 coach_state 의 history 에 둔 센서 평활용 값은 입력 기록에서
창마다 다시 계산한다(시뮬레이터가 1초마다 부르므로 같은 값이 된다).
"""

from __future__ import annotations

from typing import Any, NamedTuple

from coach.config import criteria_version as _criteria_version
from coach.schemas import IssueCriteria, JudgmentIssue, JudgmentResult, TallyItem
from coach.vocab import FeedbackType, Issue

from ._common import HIGHER, TICK_MS, In, Section, clean, parse, ramp, ratio

VERSION = "gaze-0.1"
"""연구용 대역 버전."""

#: 1초 상태 → 집계 키
_TALLY_KEY = {
    "CAMERA": "camera_ms",
    "SCREEN": "screen_ms",
    "BOTTOM": "script_ms",
    "SCRIPT": "script_ms",
    "OTHER": "away_ms",
    "UNCERTAIN": "uncertain_ms",
    "UNMEASURED": "unmeasured_ms",
}
_MEASURED = ("camera_ms", "screen_ms", "script_ms", "away_ms")
_METRICS = (
    "script_ratio",
    "script_ratio_short",
    "audience_ratio",
    "measured_ratio",
    "uncertain_ratio",
    "script_run_ms",
    "mean_reliability",
)


class Config(Section):
    """지금 `GazeConfig` 의 값 그대로. 뒤의 둘은 계약의 대본 의존 집계에 쓰는 값이다."""

    window_ms: int = 10_000
    script_labels: list[str] = ["BOTTOM", "SCRIPT"]
    camera_label: str = "CAMERA"
    uncertain_label: str = "UNCERTAIN"
    unmeasured_label: str = "UNMEASURED"
    record_stale_ms: int = 2_000
    min_window_ms: int = 5_000
    script_ratio: float = 0.7
    script_ratio_bad: float = 0.95
    continuous_ms: int = 5_000
    continuous_bad_ms: int = 15_000
    max_uncertain_ratio: float = 0.5
    sensor_smoothing_ms: int = 10_000
    streak_min_ratio: float = 0.6
    indicator_script_ratio: float = 0.5
    #: 그 1초에 이만큼 이상 말했으면 말한 1초로 본다
    speaking_min_voiced_ms: int = 300
    #: 말하면서 대본을 이만큼 이어서 봐야 reading_runs 1개
    reading_run_min_ms: int = 3_000


DEFAULT = Config()


class GazeRecord(In):
    t_ms: int
    duration_ms: int = 1_000
    state: str
    direction: str | None = None
    confidence: float | None = None
    reliability: float | None = None
    issues: list[str] = []
    frames: int | None = None


class Voiced(In):
    t_ms: int
    voiced_ms: int = 0


class GazeInput(In):
    records: list[GazeRecord] = []
    voiced: list[Voiced] = []
    since_ms: int = 0


class Seg(NamedTuple):
    """창 안으로 자르고 겹침을 뺀 구간. rec 은 원래 기록의 t_ms."""

    lo: int
    hi: int
    state: str
    rec: int


def criteria(config: Config = DEFAULT) -> dict[str, IssueCriteria]:
    """문제별 판정 기준. 지연은 지금 `review._lag` 와 같다."""
    return {
        Issue.GAZE_ON_SCRIPT: IssueCriteria(
            metric="script_run_ms",
            direction=HIGHER,
            threshold=config.continuous_ms,
            bad=config.continuous_bad_ms,
            onset_lag_ms=round(config.script_ratio * config.window_ms),
            offset_lag_ms=round((1 - config.script_ratio) * config.window_ms),
        )
    }


def summarize(
    tally: dict[str, dict[str, float]], config: Config = DEFAULT
) -> dict[str, dict[str, float | None]]:
    """합친 집계를 응시 비율로 바꾼다. 분모가 0 이면 None."""
    g = tally.get("GAZE", {})
    measured = g.get("measured_ms", 0)
    return {
        "GAZE": {
            "audience_ratio": ratio(g.get("camera_ms", 0), measured),
            "script_ratio": ratio(g.get("script_ms", 0), measured),
            "screen_ratio": ratio(g.get("screen_ms", 0), measured),
            "away_ratio": ratio(g.get("away_ms", 0), measured),
            "reading_ratio": ratio(g.get("reading_ms", 0), g.get("speaking_ms", 0)),
            "reading_runs": g.get("reading_runs", 0),
            "measured_ratio": ratio(measured, g.get("total_ms", 0)),
        }
    }


def judge(
    inputs: GazeInput | dict[str, Any], t_ms: int, config: Config = DEFAULT
) -> list[JudgmentResult]:
    """t_ms 시점의 시선 판정. 결과는 항상 하나다."""
    inp = parse(GazeInput, inputs)
    cfg = config
    common: dict[str, Any] = {
        "evaluator": "gaze",
        "area": FeedbackType.GAZE,
        "t_ms": t_ms,
        "counted_until_ms": max(t_ms, inp.since_ms),
        "criteria_version": _criteria_version(VERSION, cfg),
        "tally": _tally(inp, t_ms, cfg),
    }
    metrics: dict[str, float | None] = dict.fromkeys(_METRICS)

    ratios, span, label, label_ms = _window(inp.records, t_ms, cfg)
    if span <= 0:
        # Take 시작 순간 — 기록으로 잴 시간이 아직 없다
        return [JudgmentResult(**common, measurable=False, state="UNMEASURABLE", metrics=metrics)]

    uncertain = _uncertain(ratios, cfg)
    valid = 1.0 - uncertain
    # 나빠질 때는 바로, 좋아질 때는 천천히 — 지금 값과 최근 평균 중 나쁜 쪽. 지난 값은 지금
    # 평가기가 history 에 4자리로 반올림해 남긴 것과 같게 다시 계산한다
    prev: list[float] = []
    k = 1
    while t_ms - k * TICK_MS > max(0, t_ms - cfg.sensor_smoothing_ms):
        past, _, _, _ = _window(inp.records, t_ms - k * TICK_MS, cfg)
        prev.append(round(_uncertain(past, cfg), 4))
        k += 1
    smoothed = max(uncertain, (sum(prev) + uncertain) / (len(prev) + 1))
    sensor_ok = smoothed <= cfg.max_uncertain_ratio
    metrics["uncertain_ratio"] = round(smoothed, 4)
    metrics["measured_ratio"] = round(valid, 4)
    metrics["mean_reliability"] = _mean_reliability(inp.records, t_ms, cfg)
    if valid <= 0.0:
        # 창 전체를 측정하지 못했다
        metrics["script_run_ms"] = 0
        return [JudgmentResult(**common, measurable=False, state="UNMEASURABLE", metrics=metrics)]

    script_ratio = min(1.0, sum(ratios.get(lab, 0.0) for lab in cfg.script_labels) / valid)
    run_ms = label_ms if label in cfg.script_labels else 0
    # 센서를 믿을 수 없으면 지표를 비운다 — 틀린 숫자로 '효과 없음'을 판정하지 않게
    shown = round(script_ratio, 4) if sensor_ok else None
    metrics.update(
        script_ratio=shown,
        script_ratio_short=shown,
        audience_ratio=round(min(1.0, ratios.get(cfg.camera_label, 0.0) / valid), 4),
        script_run_ms=run_ms,
    )
    if not sensor_ok:
        state = "UNMEASURABLE"
    elif script_ratio >= cfg.indicator_script_ratio:
        state = "SCRIPT"
    else:
        state = "AUDIENCE"

    issues: list[JudgmentIssue] = []
    by_ratio = script_ratio >= cfg.script_ratio
    by_streak = run_ms >= cfg.continuous_ms and script_ratio >= cfg.streak_min_ratio
    # Take 시작 직후에는 몇 초의 표본으로 낸 비율이 크게 흔들려 지적하지 않는다
    if t_ms >= cfg.min_window_ms and (by_ratio or by_streak):
        severity = max(
            ramp(script_ratio, cfg.script_ratio, cfg.script_ratio_bad) if by_ratio else 0.0,
            ramp(run_ms, cfg.continuous_ms, cfg.continuous_bad_ms) if by_streak else 0.0,
        )
        issues.append(
            JudgmentIssue(
                issue_type=Issue.GAZE_ON_SCRIPT,
                area=FeedbackType.GAZE,
                severity=severity,
                confidence=max(0.0, 1.0 - smoothed),
                persistence_sec=run_ms / 1000,
                threshold=cfg.continuous_ms,
                bad=cfg.continuous_bad_ms,
                # 센서를 믿을 수 없어도 낸다 — 코치가 SENSOR_UNUSABLE 로 남기게 (다음 PR 에서 바꿈)
                actionable=sensor_ok,
                evidence={
                    "script_ratio": round(script_ratio, 4),
                    "script_run_ms": run_ms,
                    "uncertain_ratio": round(smoothed, 4),
                    "window_ms": cfg.window_ms,
                },
            )
        )
    return [
        JudgmentResult(**common, measurable=sensor_ok, state=state, metrics=metrics, issues=issues)
    ]


def _segments(records: list[GazeRecord], start: int, end: int) -> list[Seg]:
    """[start, end) 안으로 자르고 앞 기록에 덮인 시간을 뺀 구간. 같은 1초는 먼저 온 것을 쓴다."""
    segs: list[Seg] = []
    covered_until = start
    for r in sorted(records, key=lambda r: r.t_ms):
        lo, hi = max(r.t_ms, covered_until), min(r.t_ms + r.duration_ms, end)
        if hi <= lo:
            continue
        segs.append(Seg(lo, hi, r.state, r.t_ms))
        covered_until = hi
    return segs


def _window(
    records: list[GazeRecord], t_ms: int, cfg: Config
) -> tuple[dict[str, float], int, str | None, int]:
    """(라벨 → 비율, 창 길이 ms, 지금 라벨, 지금 라벨이 이어진 ms).

    지금 `evaluators.gaze.window_summary` 의 records 경로와 같다. 기록이 없는 시간과 UNMEASURED
    는 UNCERTAIN 으로 센다.
    """
    start, end = max(0, t_ms - cfg.window_ms), t_ms
    span = end - start
    if span <= 0:
        return {}, 0, None, 0
    segs = [
        Seg(s.lo, s.hi, cfg.uncertain_label if s.state == cfg.unmeasured_label else s.state, s.rec)
        for s in _segments(records, start, end)
    ]
    by_label: dict[str, float] = {}
    for s in segs:
        by_label[s.state] = by_label.get(s.state, 0.0) + (s.hi - s.lo)
    missing = span - sum(by_label.values())
    if missing > 0:
        by_label[cfg.uncertain_label] = by_label.get(cfg.uncertain_label, 0.0) + missing
    ratios = {label: ms / span for label, ms in by_label.items()}

    last_end = segs[-1].hi if segs else start
    if not segs or end - last_end > cfg.record_stale_ms:
        # 최근 기록이 끊겼다 — 지금 라벨은 측정하지 못한 것이다
        return ratios, span, cfg.uncertain_label, end - last_end
    run_start, current = segs[-1].lo, segs[-1].state
    for s in reversed(segs[:-1]):
        if s.state != current or s.hi != run_start:
            break
        run_start = s.lo
    return ratios, span, current, last_end - run_start


def _uncertain(ratios: dict[str, float], cfg: Config) -> float:
    return min(1.0, ratios.get(cfg.uncertain_label, 0.0) + ratios.get(cfg.unmeasured_label, 0.0))


def _mean_reliability(records: list[GazeRecord], t_ms: int, cfg: Config) -> float | None:
    """창과 겹친 기록의 reliability 평균. 값이 없는 기록은 1.0 으로 본다."""
    rel: dict[int, float] = {}
    for r in records:  # 같은 1초가 두 번 오면 먼저 온 것 (상태와 같은 규칙)
        rel.setdefault(r.t_ms, 1.0 if r.reliability is None else r.reliability)
    segs = _segments(records, max(0, t_ms - cfg.window_ms), t_ms)
    return round(sum(rel[s.rec] for s in segs) / len(segs), 4) if segs else None


def _tally(inp: GazeInput, t_ms: int, cfg: Config) -> list[TallyItem]:
    """[since_ms, t_ms) 의 집계. 기록이 덮지 못한 시간은 측정 못 한 시간으로 센다."""
    since = inp.since_ms
    if t_ms <= since:
        return []
    voiced: dict[int, int] = {}
    for v in inp.voiced:
        voiced.setdefault(v.t_ms, v.voiced_ms)

    def reading(s: Seg) -> bool:
        return s.state in cfg.script_labels and voiced.get(s.rec, 0) >= cfg.speaking_min_voiced_ms

    items: list[TallyItem] = []
    pos = since
    # 마지막 항목은 끝까지의 빈틈을 세기 위한 표지
    for s in [*_segments(inp.records, since, t_ms), Seg(t_ms, t_ms, "", 0)]:
        if s.lo > pos:
            gap = s.lo - pos
            items.append(TallyItem(t_ms=pos, values={"unmeasured_ms": gap, "total_ms": gap}))
        pos = s.hi
        length = s.hi - s.lo
        if length <= 0:
            continue
        key = _TALLY_KEY.get(s.state, "uncertain_ms")
        values = {key: length, "total_ms": length}
        if key in _MEASURED:
            values["measured_ms"] = length
        if reading(s):
            values["reading_ms"] = length
        items.append(TallyItem(t_ms=s.lo, values=clean(values)))
    # 말한 시간은 그 1초에 시선 기록이 있는지와 따로 센다 (기록이 없으면 시선만 측정 못 한 시간)
    for start, ms in sorted(voiced.items()):
        lo, hi = max(start, since), min(start + TICK_MS, t_ms)
        if hi > lo and ms >= cfg.speaking_min_voiced_ms:
            items.append(TallyItem(t_ms=lo, values={"speaking_ms": hi - lo}))

    # 말하면서 대본을 이어서 본 구간이 끝나면 reading_runs 1개. 아직 이어지는 구간(끝이 t_ms)은
    # 세지 않는다. 구간 길이는 받은 기록 안에서만 안다
    run: list[int] | None = None
    runs: list[list[int]] = []
    for s in _segments(inp.records, 0, t_ms):
        if reading(s):
            if run is not None and run[1] == s.lo:
                run[1] = s.hi
            else:
                run = [s.lo, s.hi]
                runs.append(run)
        else:
            run = None
    for lo, hi in runs:
        if hi - lo >= cfg.reading_run_min_ms and since <= hi < t_ms:
            items.append(TallyItem(t_ms=hi, values={"reading_runs": 1}))
    return sorted(items, key=lambda i: i.t_ms)
