"""1초 기록 → 1초 판정 결과 (서버 판정 모듈).

브라우저 엔진이 프레임을 1초 기록으로 모아 보내면, 코치가 요청마다 이 모듈을 부른다. 프레임은 기기를
떠나지 않으므로 서버가 받는 첫 데이터가 1초 기록이다(gaze-tracking.md 3 · 6-2절)::

    records ─ read_records ─ _timeline ─┬─ _tally         새로 센 1초마다 상태별 ms
                                        │                 · 말한 시간 · 대본 의존
    voiced  ─ _Voiced ──────────────────┤─ _window ×2    짧은 창 · 최근 창의 비율
                                        │                · 측정 비율 · 평균 신뢰도
                                        ├─ _run_at       지금 이어지는 대본 · 화면 · 그 밖 응시
                                        └─ _issues       measurable · state · 다섯 문제
                                                         → JudgmentResult

- 기록이 오지 않은 시간과 버린 기록의 시간은 UNMEASURED 다. 비율은 모두 측정된 시간
  (CAMERA · SCREEN · BOTTOM · OTHER)으로 나눈다. 재지 못한 시간이 "청중을 안 봤다"로
  읽히지 않게 하기 위해서다.
- 시간은 입력의 t_ms 뿐이다. 요청 t_ms 는 마지막 1초 기록이 끝난 시각
  (그 기록의 t_ms + duration_ms)이다(architecture.md 1-4 예시). 그래서 다음 요청의
  커서(= 이번 t_ms)부터 오는 기록이 새것이다.
- 말을 걸지 결정하지 않는다. 문제와 지표만 내고, 말할지는 코치가 정한다.

기준값은 모두 `config.GazeConfig` 에 있다. 장 · Take 지표(`summarize`)와
판정 기준(`criteria`)도 여기 있다.
"""

from __future__ import annotations

import bisect
import hashlib
import json
import math
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from .config import DEFAULT, INPUT_WINDOW_MS, GazeConfig
from .schemas import (
    GazeInput,
    GazeMetrics,
    GazeRecord,
    GazeSummary,
    IssueCriteria,
    JudgmentIssue,
    JudgmentResult,
    Summary,
    Tally,
    TallyItem,
    Voiced,
)
from .version import FEATURE_VERSION

#: 측정된 상태 (비율의 분모). 이 순서가 '가장 많이 본 곳'의 동점 순서다(gaze-tracking.md 5-2)
MEASURED_STATES: tuple[str, ...] = ("CAMERA", "SCREEN", "BOTTOM", "OTHER")
#: 엔진 상태 → tally 키
TALLY_KEY: dict[str, str] = {
    "CAMERA": "camera_ms",
    "SCREEN": "screen_ms",
    "BOTTOM": "script_ms",
    "OTHER": "away_ms",
    "UNCERTAIN": "uncertain_ms",
    "UNMEASURED": "unmeasured_ms",
}
#: 엔진 상태 → 판정 상태
JUDGED_STATE: dict[str, str] = {
    "CAMERA": "AUDIENCE",
    "SCREEN": "SCREEN",
    "BOTTOM": "SCRIPT",
    "OTHER": "AWAY",
}
#: 빈 시간을 tally 에서 나누는 단위, 발화 시간 한 개가 덮는 길이
SECOND_MS = 1000


def r4(value: float | None) -> float | None:
    """소수 4자리로 반올림(0.5 는 올림) -- TypeScript 쪽과 같은 산술."""
    if value is None:
        return None
    return math.floor(float(value) * 10000.0 + 0.5) / 10000.0


def _ratio(num: float, den: float) -> float | None:
    return num / den if den > 0 else None


def severity(value: float, threshold: float, bad: float) -> float:
    """공통 심각도: 기준값에서 0.5, 아주 나쁨 값에서 1.0, 그 사이 직선, [0, 1] 로 자른다.

    나쁜 방향은 bad 가 threshold 보다 크냐 작냐로 정해진다(architecture.md 7절).
    """
    s = 0.5 + 0.5 * (value - threshold) / (bad - threshold)
    return r4(min(1.0, max(0.0, s)))  # type: ignore[return-value]


def criteria_version(config: GazeConfig = DEFAULT) -> str:
    """`gaze-<버전>+<config 해시 12자리>` (gaze-tracking.md 9절, 코치 config.py 와 같은 식)."""
    blob = json.dumps(config.model_dump(mode="json"), sort_keys=True, ensure_ascii=False)
    return f"{FEATURE_VERSION}+{hashlib.sha256(blob.encode('utf-8')).hexdigest()[:12]}"


# --------------------------------------------------------------------------
# 읽기 · 타임라인
# --------------------------------------------------------------------------


def read_records(raw: Iterable[Any]) -> tuple[list[GazeRecord], int]:
    """요청의 1초 기록 목록 → 읽은 기록과 버린 수.

    읽을 수 없는 기록(객체가 아님, state 가 없거나 모르는 값, confidence · reliability 가 0–1 밖,
    t_ms · frames 가 음수, duration_ms 가 0 이하)은 그 1초만 버린다(gaze-tracking.md 5-2).
    기록 하나 때문에 요청 전체의 시선을 잃지 않게 하기 위해서다. 버린 시간은 UNMEASURED 가 된다.
    """
    out: list[GazeRecord] = []
    dropped = 0
    for item in raw:
        try:
            out.append(GazeRecord.model_validate(item))
        except ValidationError:
            dropped += 1
    return out, dropped


@dataclass(frozen=True)
class _Seg:
    """타임라인 조각: 기록 하나, 또는 기록 사이의 빈 시간(record False, 상태 UNMEASURED)."""

    t_ms: int
    end_ms: int
    state: str
    reliability: float = 0.0
    record: bool = True


def _gap(t_ms: int, end_ms: int) -> _Seg:
    return _Seg(t_ms, end_ms, "UNMEASURED", record=False)


def _timeline(records: list[GazeRecord], lo: int, t_ms: int) -> list[_Seg]:
    """기록을 시각 순으로 늘어놓고 빈 시간을 UNMEASURED 로 채운 [시작, t_ms) 의 조각들.

    - t_ms 이후에 시작하는 기록은 버리고, t_ms 를 넘는 기록은 t_ms 에서 자른다.
    - 앞 기록과 겹치는 기록은 버린다. 같은 1초가 두 번 오면 먼저 온 것이 남는다(정렬이 안정적이다).
    - 빈 시간은 길이와 상관없이 조각 하나다. 시각이 이상한 기록이 와도 계산량이 늘지 않는다.
    시작은 lo 와 첫 기록 중 이른 쪽이다(대본 의존 구간은 받은 기록 전체에서 잰다).
    """
    kept: list[_Seg] = []
    for r in sorted((r for r in records if r.t_ms < t_ms), key=lambda r: r.t_ms):
        if kept and r.t_ms < kept[-1].end_ms:
            continue
        kept.append(_Seg(r.t_ms, min(r.t_ms + r.duration_ms, t_ms), r.state, r.reliability))
    cursor = min(lo, kept[0].t_ms) if kept else lo
    out: list[_Seg] = []
    for seg in kept:
        if seg.t_ms > cursor:
            out.append(_gap(cursor, seg.t_ms))
        out.append(seg)
        cursor = seg.end_ms
    if t_ms > cursor:
        out.append(_gap(cursor, t_ms))
    return out


def _pieces(seg: _Seg, lo: int, hi: int) -> Iterator[tuple[int, int]]:
    """조각의 [lo, hi) 안 부분을 1초씩. 기록은 그대로 하나, 빈 시간은 시작부터 1초 단위로 나눈다."""
    a, b = max(seg.t_ms, lo), min(seg.end_ms, hi)
    if b <= a:
        return
    if seg.record:
        yield a, b
        return
    p = seg.t_ms + (a - seg.t_ms) // SECOND_MS * SECOND_MS
    while p < b:
        yield max(p, a), min(p + SECOND_MS, b)
        p += SECOND_MS


class _Voiced:
    """말한 1초들(voiced_ms 가 기준 이상인 1초)을 이어 붙인 구간.
    시각 · 구간이 얼마나 말한 때인지 답한다.

    발화 시간 한 개는 그 t_ms 부터 1초를 덮는다(음량 1초 기록과 시선 1초 기록은 같은 t_ms 격자다).
    """

    def __init__(self, voiced: list[Voiced], cfg: GazeConfig) -> None:
        first: dict[int, int] = {}
        for v in voiced:  # 같은 1초가 두 번 오면 먼저 온 것
            first.setdefault(v.t_ms, v.voiced_ms)
        spans: list[list[int]] = []
        for t in sorted(t for t, ms in first.items() if ms >= cfg.speaking_min_voiced_ms):
            if spans and t <= spans[-1][1]:
                spans[-1][1] = max(spans[-1][1], t + SECOND_MS)
            else:
                spans.append([t, t + SECOND_MS])
        self._spans = spans
        self._starts = [s for s, _ in spans]

    def at(self, p: int) -> bool:
        """시각 p 가 말한 1초 안인가."""
        i = bisect.bisect_right(self._starts, p) - 1
        return i >= 0 and p < self._spans[i][1]

    def overlap(self, a: int, b: int) -> int:
        """[a, b) 중 말한 1초에 든 ms."""
        total = 0
        i = max(0, bisect.bisect_right(self._starts, a) - 1)
        while i < len(self._spans) and self._spans[i][0] < b:
            s, e = self._spans[i]
            total += max(0, min(b, e) - max(a, s))
            i += 1
        return total


def _is_reading(seg: _Seg, spoken: _Voiced) -> bool:
    """말한 1초이면서 대본 응시(BOTTOM)인 기록."""
    return seg.record and seg.state == "BOTTOM" and spoken.at(seg.t_ms)


# --------------------------------------------------------------------------
# 새 몫 (tally) · 대본 의존 구간
# --------------------------------------------------------------------------


def _tally(
    segs: list[_Seg], since_ms: int, t_ms: int, spoken: _Voiced, cfg: GazeConfig
) -> list[TallyItem]:
    """since_ms 이상인 1초마다 상태별 ms · 측정 · 전체 · 말한 시간 · 대본 의존
    (gaze-tracking.md 6-2 3번).

    기록은 t_ms 가 since_ms 이상일 때 통째로 새것이다. 빈 시간은 [since_ms, t_ms) 안을
    빈 시간의 시작부터 1초씩 낸다(코치가 각 1초를 그 시각의 장에 더한다). 다만 입력 창(30초)보다
    오래된 빈 시간은 한 조각이다 -- 요청이 오래 빠진 뒤에도 몫의 개수가 시각 차이만큼 늘지 않게
    한다(그 시간은 코치가 복구로 다시 센다).
    대본 의존 구간이 끝나면 끝난 1초에 reading_runs 1 을 더한다.
    """
    split_from = max(since_ms, t_ms - INPUT_WINDOW_MS)
    items: dict[int, dict[str, int]] = {}
    for seg in segs:
        if seg.record:
            parts = [(seg.t_ms, seg.end_ms)] if since_ms <= seg.t_ms < t_ms else []
        else:
            old = (max(seg.t_ms, since_ms), min(seg.end_ms, split_from))
            parts = [old] if old[1] > old[0] else []
            parts += _pieces(seg, split_from, t_ms)
        for a, b in parts:
            ms = b - a
            values = {TALLY_KEY[seg.state]: ms, "total_ms": ms}
            if seg.state in MEASURED_STATES:
                values["measured_ms"] = ms
            speaking = spoken.overlap(a, b)
            if speaking:
                values["speaking_ms"] = speaking
                if seg.state == "BOTTOM":
                    values["reading_ms"] = speaking
            items[a] = values
    for end in _reading_run_ends(segs, spoken, cfg):
        if since_ms <= end < t_ms:
            values = items.setdefault(end, {})
            values["reading_runs"] = values.get("reading_runs", 0) + 1
    return [TallyItem(t_ms=t, values=v) for t, v in sorted(items.items())]


def _reading_run_ends(segs: list[_Seg], spoken: _Voiced, cfg: GazeConfig) -> list[int]:
    """말한 1초 동안 대본 응시가 기준 시간 이상 이어졌다가 끝난 구간의 끝 시각
    (gaze-tracking.md 6-3).

    말하지 않은 1초 · 다른 상태 · 빈 시간이 끼면 끊긴다.
    판정 시각까지 이어지는 구간은 끝나지 않았으므로
    세지 않는다(Take 끝까지 이어진 구간도 같다). 구간 길이는 받은 기록 안에서만 안다.
    """
    ends: list[int] = []
    start: int | None = None
    end = 0
    for seg in segs:
        if _is_reading(seg, spoken):
            if start is None:
                start = seg.t_ms
            end = seg.end_ms
            continue
        if start is not None and end - start >= cfg.reading_run_min_ms:
            ends.append(end)
        start = None
    return ends


# --------------------------------------------------------------------------
# 창 · 이어짐
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class _Window:
    span_ms: int
    state_ms: dict[str, int]
    measured_ms: int
    speaking_ms: int
    reading_ms: int
    #: 창 안 1초 기록들의 reliability 평균 (기록이 없으면 0)
    mean_reliability: float

    def ratio(self, state: str) -> float | None:
        return _ratio(self.state_ms[state], self.measured_ms)

    @property
    def measured_ratio(self) -> float:
        return self.measured_ms / self.span_ms if self.span_ms > 0 else 0.0


def _window(segs: list[_Seg], lo: int, t_ms: int, spoken: _Voiced) -> _Window:
    """[lo, t_ms) 의 상태별 ms · 말한 시간 · 대본 의존 시간,
    창에 걸친 1초 기록들의 reliability 평균."""
    state_ms = dict.fromkeys(TALLY_KEY, 0)
    rel_sum = 0.0
    rel_n = speaking = reading = 0
    for seg in segs:
        for a, b in _pieces(seg, lo, t_ms):
            state_ms[seg.state] += b - a
            if seg.record:
                rel_sum += seg.reliability
                rel_n += 1
            ms = spoken.overlap(a, b)
            speaking += ms
            if seg.state == "BOTTOM":
                reading += ms
    return _Window(
        span_ms=max(0, t_ms - lo),
        state_ms=state_ms,
        measured_ms=sum(state_ms[s] for s in MEASURED_STATES),
        speaking_ms=speaking,
        reading_ms=reading,
        mean_reliability=rel_sum / rel_n if rel_n else 0.0,
    )


def _unusable_ms(segs: list[_Seg], lo: int, t_ms: int, min_reliability: float) -> int:
    """[lo, t_ms) 안에서 쓸 수 없던 시간의 합: 기록이 없거나 측정 상태가 아니거나(UNCERTAIN) 기록의
    reliability 가 기준 미만인 1초. 측정 불가(GAZE_UNMEASURABLE)가 이어진 시간으로 낸다.

    측정 불가는 창 전체의 비율 · 평균으로 정하는 문제라, 측정 비율과 같은 방식(창 안의 합)으로 잰다.
    """
    total = 0
    for seg in segs:
        usable = seg.record and seg.state in MEASURED_STATES and seg.reliability >= min_reliability
        if not usable:
            total += sum(b - a for a, b in _pieces(seg, lo, t_ms))
    return total


def _run_at(segs: list[_Seg], t_ms: int) -> dict[str, int]:
    """판정 시각까지 지금 이어지는 응시 길이: {BOTTOM, SCREEN, OTHER} → ms (이어지지 않으면 0).

    같은 상태의 기록이 빈틈없이 이어진 것이다.
    다른 상태 · UNCERTAIN · 빈 시간이 끼면 끊기고, OTHER 는
    방향이 달라도 이어진다.
    """
    runs = {"BOTTOM": 0, "SCREEN": 0, "OTHER": 0}
    if not segs or segs[-1].end_ms != t_ms:
        return runs
    last = segs[-1]
    if not last.record or last.state not in runs:
        return runs
    start = last.t_ms
    for seg in reversed(segs[:-1]):
        if not seg.record or seg.state != last.state or seg.end_ms != start:
            break
        start = seg.t_ms
    runs[last.state] = t_ms - start
    return runs


# --------------------------------------------------------------------------
# judge
# --------------------------------------------------------------------------


def judge(
    inputs: GazeInput | dict[str, Any], t_ms: int, config: GazeConfig = DEFAULT
) -> list[JudgmentResult]:
    """t_ms 시점의 시선 판정. 문제가 없어도 결과 하나(영역 GAZE)를 담은 목록을 낸다.

    같은 입력에는 같은 결과가 나오고,
    돌려준 counted_until_ms 로 다시 부르면 이미 센 몫은 나오지 않는다.
    요청이 빠져 새 1초가 여러 개면 한 번에 모두 센다(gaze-tracking.md 6-2).
    """
    cfg = config
    inp = inputs if isinstance(inputs, GazeInput) else GazeInput.model_validate(inputs)
    t = int(t_ms)
    since = inp.since_ms
    records, _ = read_records(inp.records)
    spoken = _Voiced(inp.voiced, cfg)
    short_lo = max(0, t - cfg.short_window_ms)
    recent_lo = max(0, t - cfg.recent_window_ms)
    segs = _timeline(records, min(recent_lo, since), t)

    short = _window(segs, short_lo, t, spoken)
    recent = _window(segs, recent_lo, t, spoken)
    runs = _run_at(segs, t)
    # 측정 불가가 이어진 시간은 측정 불가를 가른 창에서 잰다: 최근 창의 측정 비율이 짧은 창보다
    # 나빠 기준 미만이 됐으면 최근 창, 그 밖(짧은 창의 측정 비율 · 평균 신뢰도)은 짧은 창
    by_recent = (
        recent.measured_ratio < short.measured_ratio
        and recent.measured_ratio < cfg.min_measured_ratio
    )
    unusable = _unusable_ms(segs, recent_lo if by_recent else short_lo, t, cfg.min_mean_reliability)
    metrics = GazeMetrics(
        audience_ratio=r4(recent.ratio("CAMERA")),
        screen_ratio=r4(recent.ratio("SCREEN")),
        script_ratio=r4(recent.ratio("BOTTOM")),
        away_ratio=r4(recent.ratio("OTHER")),
        reading_ratio=r4(_ratio(recent.reading_ms, recent.speaking_ms)),
        audience_ratio_short=r4(short.ratio("CAMERA")),
        script_ratio_short=r4(short.ratio("BOTTOM")),
        away_ratio_short=r4(short.ratio("OTHER")),
        measured_ratio=r4(short.measured_ratio),  # type: ignore[arg-type]
        mean_reliability=r4(short.mean_reliability),  # type: ignore[arg-type]
        script_run_ms=runs["BOTTOM"],
        screen_run_ms=runs["SCREEN"],
        away_run_ms=runs["OTHER"],
    )
    # 측정 비율은 지금 짧은 창과 최근 창 중 나쁜 쪽
    # -- 잡음으로 잠깐 좋아진 1초에 판정을 다시 열지 않게
    measured = min(short.measured_ratio, recent.measured_ratio)
    measurable = (
        measured >= cfg.min_measured_ratio and short.mean_reliability >= cfg.min_mean_reliability
    )
    return [
        JudgmentResult(
            t_ms=t,
            counted_until_ms=max(t, since),
            criteria_version=criteria_version(cfg),
            measurable=measurable,
            state=_state(measurable, short, cfg),
            metrics=metrics,
            tally=_tally(segs, since, t, spoken, cfg),
            issues=_issues(t, measurable, measured, short, recent, runs, unusable, metrics, cfg),
        )
    ]


def _state(measurable: bool, short: _Window, cfg: GazeConfig) -> str:
    """짧은 창에서 가장 많이 본 곳. 대본 응시 비율이 기준 이상이면 SCRIPT.
    마지막 1초로 정하지 않는다."""
    if not measurable:
        return "UNMEASURABLE"
    script = short.ratio("BOTTOM")
    if script is not None and script >= cfg.state_script_ratio:
        return "SCRIPT"
    top = max(MEASURED_STATES, key=lambda s: (short.state_ms[s], -MEASURED_STATES.index(s)))
    return JUDGED_STATE[top]


def _issues(
    t_ms: int,
    measurable: bool,
    measured: float,
    short: _Window,
    recent: _Window,
    runs: dict[str, int],
    unusable_ms: int,
    m: GazeMetrics,
    cfg: GazeConfig,
) -> list[JudgmentIssue]:
    """다섯 문제(gaze-tracking.md 5-4).
    잴 수 없으면 측정 불가 신호만, Take 시작 직후에는 아무것도."""
    if not measurable:
        bad = cfg.unmeasurable_bad
        return [
            JudgmentIssue(
                issue_type="GAZE_UNMEASURABLE",
                severity=max(
                    severity(measured, cfg.min_measured_ratio, bad),
                    severity(short.mean_reliability, cfg.min_mean_reliability, bad),
                ),
                confidence=1.0,
                persistence_sec=unusable_ms / 1000,
                threshold=cfg.min_measured_ratio,
                bad=bad,
                evidence={
                    "measured_ratio": r4(measured),
                    "mean_reliability": m.mean_reliability,
                },
                actionable=False,
            )
        ]
    if t_ms < cfg.take_start_quiet_ms:
        return []

    issues: list[JudgmentIssue] = []
    short_conf = r4(short.measured_ratio)

    def run_issue(issue_type: str, state: str, threshold: int, bad: int, evidence: dict) -> None:
        issues.append(
            JudgmentIssue(
                issue_type=issue_type,  # type: ignore[arg-type]
                severity=severity(runs[state], threshold, bad),
                confidence=short_conf,  # type: ignore[arg-type]
                persistence_sec=runs[state] / 1000,
                threshold=threshold,
                bad=bad,
                evidence=evidence,
                actionable=True,
            )
        )

    script_short = short.ratio("BOTTOM") or 0.0
    if runs["BOTTOM"] >= cfg.script_run_ms and script_short >= cfg.script_short_ratio:
        run_issue(
            "GAZE_ON_SCRIPT",
            "BOTTOM",
            cfg.script_run_ms,
            cfg.script_run_bad_ms,
            {"script_run_ms": m.script_run_ms, "script_ratio_short": m.script_ratio_short},
        )
    if runs["SCREEN"] >= cfg.screen_run_ms:
        run_issue(
            "GAZE_ON_SCREEN",
            "SCREEN",
            cfg.screen_run_ms,
            cfg.screen_run_bad_ms,
            {"screen_run_ms": m.screen_run_ms},
        )
    if runs["OTHER"] >= cfg.away_run_ms:
        run_issue(
            "GAZE_AWAY",
            "OTHER",
            cfg.away_run_ms,
            cfg.away_run_bad_ms,
            {"away_run_ms": m.away_run_ms, "away_ratio_short": m.away_ratio_short},
        )
    audience = recent.ratio("CAMERA")
    if (
        recent.measured_ms >= cfg.low_eye_contact_min_measured_ms
        and audience is not None
        and audience < cfg.low_eye_contact_ratio
    ):
        issues.append(
            JudgmentIssue(
                issue_type="GAZE_LOW_EYE_CONTACT",
                severity=severity(audience, cfg.low_eye_contact_ratio, cfg.low_eye_contact_bad),
                confidence=r4(recent.measured_ratio),  # type: ignore[arg-type]
                persistence_sec=recent.measured_ms / 1000,
                threshold=cfg.low_eye_contact_ratio,
                bad=cfg.low_eye_contact_bad,
                evidence={
                    "audience_ratio": m.audience_ratio,
                    "audience_ratio_short": m.audience_ratio_short,
                },
                actionable=True,
            )
        )
    return issues


# --------------------------------------------------------------------------
# summarize · criteria
# --------------------------------------------------------------------------


def summarize(tally: Tally, config: GazeConfig = DEFAULT) -> dict[str, dict[str, Any]]:
    """장 · Take 의 tally 합계 → {"GAZE": {...}} (gaze-tracking.md 5-3 · 6-4 · 14절).

    인자: tally - 코치가 더한 합계(영역 → 이름 → 합, 빠진 키는 0). config - 쓰지 않지만 다른 진입
    함수와 같은 모양으로 받는다. 반환: `Summary` 를 dict 로 만든 것. 코치는 이 값을 고치지 않고
    Take 결과에 담는다. 분모가 0 인 비율은 None 이고, measured_ratio 만 0 이다(architecture.md 7절).
    """
    g = tally.get("GAZE", {})
    measured = g.get("measured_ms", 0)
    summary = Summary(
        GAZE=GazeSummary(
            audience_ratio=r4(_ratio(g.get("camera_ms", 0), measured)),
            script_ratio=r4(_ratio(g.get("script_ms", 0), measured)),
            screen_ratio=r4(_ratio(g.get("screen_ms", 0), measured)),
            away_ratio=r4(_ratio(g.get("away_ms", 0), measured)),
            reading_ratio=r4(_ratio(g.get("reading_ms", 0), g.get("speaking_ms", 0))),
            reading_runs=int(g.get("reading_runs", 0)),
            measured_ratio=r4(_ratio(measured, g.get("total_ms", 0)) or 0.0),  # type: ignore[arg-type]
        )
    )
    return summary.model_dump(mode="json")


def criteria(config: GazeConfig = DEFAULT) -> dict[str, IssueCriteria]:
    """다섯 문제의 판정 기준 (gaze-tracking.md 5-3 · 5-4).
    코치가 Take 결과의 criteria.GAZE 에 남긴다.

    onset_lag_ms · offset_lag_ms 는 판정 창 때문에 문제의 시작 · 끝이 늦게 잡히는 폭이다(코치가 Take
    결과의 문제 구간을 되돌릴 때 쓴다). 기준값에서 계산한다:
    - 이어짐 문제: 기준 시간이 지나야 잡히고(GAZE_ON_SCRIPT 는 짧은 창 비율도 차야 한다), 이어짐이
      끊기면 바로 끝난다.
    - 눈맞춤 부족: 청중을 전혀 안 보기 시작하면 (1 − 기준) × 최근 창 뒤에 기준 아래로
      내려가고(측정 시간 최소도 채워야 한다), 다시 청중만 보면 기준 × 최근 창 뒤에 올라온다.
    - 측정 불가: 측정이 끊기면 (1 − 기준) × 짧은 창 뒤에 잡히고, 측정 비율은 최근 창과도 비교하므로
      다시 잴 수 있게 된 뒤 기준 × 최근 창이 지나야 끝난다.
    """
    c = config
    higher, lower = "HIGHER_IS_WORSE", "LOWER_IS_WORSE"

    def run_rule(metric: str, threshold: int, bad: int, onset: int) -> IssueCriteria:
        return IssueCriteria(
            metric=metric,
            direction=higher,
            threshold=threshold,
            bad=bad,
            onset_lag_ms=onset,
            offset_lag_ms=0,
        )

    return {
        "GAZE_ON_SCRIPT": run_rule(
            "script_run_ms",
            c.script_run_ms,
            c.script_run_bad_ms,
            max(c.script_run_ms, round(c.script_short_ratio * c.short_window_ms)),
        ),
        "GAZE_ON_SCREEN": run_rule(
            "screen_run_ms", c.screen_run_ms, c.screen_run_bad_ms, c.screen_run_ms
        ),
        "GAZE_AWAY": run_rule("away_run_ms", c.away_run_ms, c.away_run_bad_ms, c.away_run_ms),
        "GAZE_LOW_EYE_CONTACT": IssueCriteria(
            metric="audience_ratio",
            direction=lower,
            threshold=c.low_eye_contact_ratio,
            bad=c.low_eye_contact_bad,
            onset_lag_ms=max(
                c.low_eye_contact_min_measured_ms,
                round((1 - c.low_eye_contact_ratio) * c.recent_window_ms),
            ),
            offset_lag_ms=round(c.low_eye_contact_ratio * c.recent_window_ms),
        ),
        "GAZE_UNMEASURABLE": IssueCriteria(
            metric="measured_ratio",
            direction=lower,
            threshold=c.min_measured_ratio,
            bad=c.unmeasurable_bad,
            onset_lag_ms=round((1 - c.min_measured_ratio) * c.short_window_ms),
            offset_lag_ms=round(c.min_measured_ratio * c.recent_window_ms),
        ),
    }
