"""리뷰 에이전트로 넘기는 근거.

리뷰 에이전트는 코치를 직접 부르지 않습니다. 코치가 1초마다 낸 이벤트를
BE 가 쌓아 두고, Take 가 끝나면 이 함수가 그 이벤트를 요약된 구조로 묶습니다.

    BE 가 쌓은 이벤트 (decide 응답의 events + finalize 응답의 events)
        + 이 Take 의 plan · missions · memory
        → build_review_evidence()
        → CoachReviewEvidence → 리뷰 에이전트 입력 (/takes/analyze 안에서)

두 층으로 나뉩니다.

- **측정 정리** — 이벤트 → 장별 누적(Agg) · 문제 구간(Seg). 코치 이벤트에서만 나온다
- **판정(assess)** — Agg · Seg → 문제 순위 · 미션 판정 · 기억 비교 · 영역 상태 · 다음 미션

판정 층은 순수 함수입니다. research 의 coach_lab/truth.py 가 시뮬레이터의 정답 데이터를
같은 모양으로 만들어 같은 판정을 돌리므로, 실험에서 '측정이 틀렸는지'와
'판정 규칙이 틀렸는지'를 나눠 볼 수 있습니다.

숫자는 전부 코드가 계산합니다. 리뷰 에이전트(LLM)는 이 숫자를 인용만 하고 새로 만들지 않습니다.
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from pydantic import TypeAdapter

from .config import DEFAULT_CONFIG, CoachConfig
from .evaluators.base import ramp
from .schemas import (
    CoachEvent,
    CoachingSummary,
    CoachReviewEvidence,
    DataQuality,
    EpisodeEvent,
    InterventionEvent,
    InterventionReview,
    IssueReview,
    Memory,
    MemoryReview,
    Mission,
    MissionReview,
    NextMission,
    NextMissionTarget,
    OutcomeEvent,
    Plan,
    SegmentReview,
    SlideEvent,
    SlideReview,
    StrategyEvent,
    StrategyReview,
    StrengthReview,
    SuppressedEvent,
    TypeStatusReview,
    TypeSummary,
)
from .version import POLICY_VERSION
from .vocab import (
    FeedbackType,
    Instruction,
    Issue,
    MemoryLabel,
    MissionStatus,
    Outcome,
    SegmentHint,
    StrategyChange,
    StrengthKind,
    TypeStatus,
)

_EVENTS = TypeAdapter(list[CoachEvent])

#: 영역 순서 — 점수가 같을 때 결과가 실행마다 달라지지 않게
TYPE_ORDER: tuple[FeedbackType, ...] = (
    FeedbackType.TIME,
    FeedbackType.GAZE,
    FeedbackType.SPEED,
    FeedbackType.VOLUME,
    FeedbackType.PAUSE,
    FeedbackType.FILLER,
    FeedbackType.CONTENT,
)


# ══════════════════════════════════════════════════════════════════════════
# 측정 정리
# ══════════════════════════════════════════════════════════════════════════


def _div(a: float, b: float, digits: int = 4) -> float | None:
    return round(a / b, digits) if b > 0 else None


@dataclass
class Agg:
    """한 장(slide_number) 또는 Take 전체(slide_number=None)의 누적."""

    slide_number: int | None
    visits: int = 0
    start_ms: int = 0
    duration_ms: int = 0
    total_ms: int = 0
    target_ms: int | None = None
    gaze_valid_ms: int = 0
    gaze_script_ms: float = 0.0
    gaze_unusable_ms: int = 0
    speech_ok_ms: int = 0
    cpm_ms: int = 0
    cpm_weighted: float = 0.0
    filler_count: int = 0
    audio_live_ms: int = 0
    speaking_ms: int = 0
    db_ms: int = 0
    db_weighted: float = 0.0
    long_silence_ms: int = 0

    def add(self, ev: SlideEvent) -> None:
        if self.visits == 0:
            self.start_ms = ev.start_ms
        self.visits += 1
        self.start_ms = min(self.start_ms, ev.start_ms)
        self.duration_ms += ev.end_ms - ev.start_ms
        self.total_ms += ev.total_ms
        if self.slide_number is not None and ev.target_ms is not None:
            self.target_ms = ev.target_ms
        for name in (
            "gaze_valid_ms",
            "gaze_script_ms",
            "gaze_unusable_ms",
            "speech_ok_ms",
            "cpm_ms",
            "cpm_weighted",
            "filler_count",
            "audio_live_ms",
            "speaking_ms",
            "db_ms",
            "db_weighted",
            "long_silence_ms",
        ):
            setattr(self, name, getattr(self, name) + getattr(ev, name))

    # ── 파생 값 ──────────────────────────────────────────────────────────
    @property
    def script_ratio(self) -> float | None:
        return _div(self.gaze_script_ms, self.gaze_valid_ms)

    @property
    def cpm(self) -> float | None:
        return _div(self.cpm_weighted, self.cpm_ms, 1)

    @property
    def voice_diff_db(self) -> float | None:
        return _div(self.db_weighted, self.db_ms, 2)

    @property
    def filler_per_min(self) -> float | None:
        if self.speech_ok_ms <= 0 or self.duration_ms < 10_000:
            return None
        return round(self.filler_count * 60_000 / self.duration_ms, 2)

    @property
    def gaze_coverage(self) -> float | None:
        return _div(self.gaze_valid_ms, self.total_ms)

    @property
    def speech_coverage(self) -> float | None:
        return _div(self.speech_ok_ms, self.total_ms)

    @property
    def audio_coverage(self) -> float | None:
        return _div(self.audio_live_ms, self.total_ms)

    @property
    def volume_coverage(self) -> float | None:
        """오디오가 살아 있던 비율과 말한 시간 중 음량을 잰 비율 중 작은 쪽.

        음량 레벨 입력은 기준이 잡히기 전(Take 시작 직후) 말한 시간을 재지 못한다 —
        오디오만 살아 있었다고 음량을 평가하면 표본 없이 '해결됨'을 판정하게 된다.
        """
        measured = _div(self.db_ms, self.speaking_ms)
        audio = self.audio_coverage
        return None if measured is None or audio is None else min(audio, measured)

    @property
    def over_ms(self) -> int | None:
        return self.duration_ms - self.target_ms if self.target_ms else None


@dataclass
class Seg:
    """문제 구간. start/end 는 탐지 구간, onset/offset 은 창 지연을 되돌린 추정 구간 (끝은 배타)."""

    area: FeedbackType
    slide_number: int | None
    issue_types: list[Issue]
    start_ms: int
    end_ms: int
    onset_ms: int
    offset_ms: int
    peak_severity: float
    mean_severity: float = 0.0
    reliable_ms: int = 0
    unreliable_ms: int = 0
    candidate_ids: list[str] = field(default_factory=list)
    intervention_ids: list[str] = field(default_factory=list)
    suppressed_reasons: list[str] = field(default_factory=list)
    evidence: dict[str, Any] = field(default_factory=dict)

    @property
    def reliability(self) -> float:
        total = self.reliable_ms + self.unreliable_ms
        return round(self.reliable_ms / total, 4) if total > 0 else 1.0

    @property
    def span_ms(self) -> int:
        return max(0, self.offset_ms - self.onset_ms)

    @property
    def burden_s(self) -> float:
        # 평균 심각도로 잰다. 최고값은 잡음 많은 지표(CPM 등)에서 부풀어 순위를 흔든다 (실험 07)
        return self.span_ms / 1000 * (self.mean_severity or self.peak_severity)


def aggregate_slides(events: list[SlideEvent]) -> tuple[dict[int, Agg], Agg]:
    slides: dict[int, Agg] = {}
    take = Agg(slide_number=None)
    for ev in sorted(events, key=lambda e: (e.start_ms, e.slide_number or 0)):
        if ev.slide_number is not None:
            slides.setdefault(ev.slide_number, Agg(slide_number=ev.slide_number)).add(ev)
        take.add(ev)
    return slides, take


def _lag(ep: EpisodeEvent, cfg: CoachConfig) -> tuple[int, int]:
    if not cfg.review.lag_compensation:
        return 0, 0
    k = cfg.review.lag_scale
    if ep.issue_type == Issue.GAZE_ON_SCRIPT:
        # 창 비율이 기준 thr 을 넘으려면 창의 thr 만큼을 대본에 써야 하고, 내려오려면 1-thr 만큼을
        # 떠나야 한다
        window = int(ep.peak_evidence.get("window_ms") or 10_000)
        thr = cfg.gaze.script_ratio
        return round(k * thr * window), round(k * (1 - thr) * window)
    onset, offset = cfg.review.lag_ms.get(ep.issue_type, (0, 0))
    return round(k * onset), round(k * offset)


def segments_from_episodes(episodes: list[EpisodeEvent], cfg: CoachConfig) -> list[Seg]:
    tick = cfg.policy.default_tick_ms
    out: list[Seg] = []
    for ep in episodes:
        start, end = ep.start_ms, ep.end_ms + tick
        lag_on, lag_off = _lag(ep, cfg)
        onset = max(0, start - lag_on)
        offset = max(onset + tick, end - lag_off)
        out.append(
            Seg(
                area=ep.area,
                slide_number=ep.slide_number,
                issue_types=[ep.issue_type],
                start_ms=start,
                end_ms=end,
                onset_ms=onset,
                offset_ms=offset,
                peak_severity=ep.peak_severity,
                mean_severity=ep.mean_severity,
                reliable_ms=ep.reliable_ms,
                unreliable_ms=ep.unreliable_ms,
                candidate_ids=[ep.candidate_id],
                intervention_ids=list(ep.intervention_ids),
                suppressed_reasons=list(ep.suppressed_reasons),
                evidence=dict(ep.peak_evidence),
            )
        )
    return out


def is_reliable(seg: Seg, cfg: CoachConfig) -> bool:
    return seg.reliability >= cfg.review.min_reliability


def merge_segments(segs: list[Seg], cfg: CoachConfig) -> list[Seg]:
    """같은 영역 · 같은 장 · 같은 신뢰도의 구간이 merge_gap_ms 안에서 다시 시작되면 하나로 합친다.

    잡음 때문에 한 번의 문제가 여러 조각으로 끊겨 리뷰가 같은 지적을 여러 번 하지 않게 한다.
    """
    gap = cfg.review.merge_gap_ms

    def key(s: Seg) -> tuple[int, int, bool]:
        return (TYPE_ORDER.index(s.area), s.slide_number or -1, is_reliable(s, cfg))

    out: list[Seg] = []
    for seg in sorted(segs, key=lambda s: (key(s), s.onset_ms)):
        last = out[-1] if out else None
        if last is not None and key(last) == key(seg) and seg.onset_ms <= last.offset_ms + gap:
            last.start_ms = min(last.start_ms, seg.start_ms)
            last.end_ms = max(last.end_ms, seg.end_ms)
            last.onset_ms = min(last.onset_ms, seg.onset_ms)
            last.offset_ms = max(last.offset_ms, seg.offset_ms)
            if seg.peak_severity > last.peak_severity:
                last.peak_severity = seg.peak_severity
                last.evidence = dict(seg.evidence)
            total = last.span_ms + seg.span_ms
            if total:
                last.mean_severity = (
                    (last.mean_severity or last.peak_severity) * last.span_ms
                    + (seg.mean_severity or seg.peak_severity) * seg.span_ms
                ) / total
            last.reliable_ms += seg.reliable_ms
            last.unreliable_ms += seg.unreliable_ms
            for name in ("issue_types", "candidate_ids", "intervention_ids", "suppressed_reasons"):
                items = getattr(last, name)
                for item in getattr(seg, name):
                    if item not in items:
                        items.append(item)
            continue
        out.append(
            Seg(
                **{
                    **seg.__dict__,
                    "issue_types": list(seg.issue_types),
                    "candidate_ids": list(seg.candidate_ids),
                    "intervention_ids": list(seg.intervention_ids),
                    "suppressed_reasons": list(seg.suppressed_reasons),
                }
            )
        )
    # 짧고 아무 일도 없던 구간은 잡음 깜빡임이다 — 리뷰가 근거 없는 지적을 하지 않게 뺀다.
    # 길이는 지연 보정 전의 '실제로 잡힌 시간'으로 잰다. 보정 뒤 길이로 재면 1초 깜빡임도
    # 창 길이만큼 부풀어 걸러지지 않는다 (실험: harsh 의 근거 없는 시선 지적)
    out = [
        s for s in out if s.intervention_ids or s.end_ms - s.start_ms >= cfg.review.min_segment_ms
    ]
    return sorted(out, key=lambda s: (s.onset_ms, TYPE_ORDER.index(s.area)))


# ══════════════════════════════════════════════════════════════════════════
# 판정 — 순수 함수. research 의 coach_lab/truth.py 가 정답 데이터에도 그대로 쓴다
# ══════════════════════════════════════════════════════════════════════════


@dataclass
class IssueAgg:
    area: FeedbackType
    slide_number: int | None
    issue_types: list[Issue]
    burden_s: float
    duration_ms: int
    segments: int
    peak_severity: float
    intervention_ids: list[str] = field(default_factory=list)
    evidence: dict[str, Any] = field(default_factory=dict)


@dataclass
class Assessment:
    issues: list[IssueReview]
    missions: list[MissionReview]
    memory: list[MemoryReview]
    type_status: list[TypeStatusReview]
    next_missions: list[NextMission]
    evaluable: dict[FeedbackType, bool]


def evaluable_types(take: Agg, plan: Plan | None, cfg: CoachConfig) -> dict[FeedbackType, bool]:
    cov = cfg.review.min_coverage

    def ok(v: float | None) -> bool:
        return v is not None and v >= cov

    has_time = take.target_ms is not None or (plan is not None and plan.target_ms is not None)
    has_slide_targets = plan is not None and any(s.target_ms for s in plan.slides)
    return {
        FeedbackType.GAZE: ok(take.gaze_coverage),
        FeedbackType.SPEED: ok(take.speech_coverage),
        FeedbackType.FILLER: ok(take.speech_coverage),
        FeedbackType.VOLUME: ok(take.volume_coverage),
        FeedbackType.PAUSE: ok(take.audio_coverage),
        FeedbackType.CONTENT: False,
        FeedbackType.TIME: has_time or has_slide_targets or take.duration_ms > 0,
    }


def collect_issues(
    slides: dict[int, Agg],
    take: Agg,
    segs: list[Seg],
    plan: Plan | None,
    cfg: CoachConfig,
) -> list[IssueAgg]:
    """문제 구간 · 장별 누적 → (영역 × 장) 문제. 시간은 장별 누적에서 바로 계산한다."""
    rc = cfg.review
    groups: dict[tuple[FeedbackType, int | None], IssueAgg] = {}

    for seg in segs:
        if seg.area in (FeedbackType.TIME, FeedbackType.CONTENT):
            continue  # 시간은 아래에서 장별 누적으로 정확히 잰다
        if rc.exclude_unreliable and not is_reliable(seg, cfg):
            continue
        k = (seg.area, seg.slide_number)
        agg = groups.get(k)
        if agg is None:
            agg = groups[k] = IssueAgg(seg.area, seg.slide_number, [], 0.0, 0, 0, 0.0)
        agg.burden_s += seg.burden_s
        agg.duration_ms += seg.span_ms
        agg.segments += 1
        for code in seg.issue_types:
            if code not in agg.issue_types:
                agg.issue_types.append(code)
        agg.intervention_ids += [i for i in seg.intervention_ids if i not in agg.intervention_ids]
        if seg.peak_severity >= agg.peak_severity:
            agg.peak_severity = seg.peak_severity
            agg.evidence = dict(seg.evidence)

    for number, s in slides.items():
        if s.target_ms and s.duration_ms > s.target_ms * rc.time_over_ratio:
            ratio = s.duration_ms / s.target_ms
            severity = ramp(ratio, rc.time_over_ratio, rc.time_bad_ratio)
            over = s.duration_ms - s.target_ms
            groups[(FeedbackType.TIME, number)] = IssueAgg(
                FeedbackType.TIME,
                number,
                [Issue.SLIDE_OVER],
                over / 1000 * severity,
                over,
                1,
                round(severity, 4),
                evidence={"duration_ms": s.duration_ms, "target_ms": s.target_ms, "over_ms": over},
            )

    if plan is not None and plan.target_ms and take.duration_ms > 0:
        limit = plan.max_ms or plan.target_ms
        if take.duration_ms > limit:
            over = take.duration_ms - limit
            groups[(FeedbackType.TIME, None)] = IssueAgg(
                FeedbackType.TIME,
                None,
                [Issue.TIME_OVER],
                over / 1000,
                over,
                1,
                1.0,
                evidence={"duration_ms": take.duration_ms, "limit_ms": limit, "over_ms": over},
            )
        elif plan.min_ms and take.duration_ms < plan.min_ms:
            short = plan.min_ms - take.duration_ms
            groups[(FeedbackType.TIME, None)] = IssueAgg(
                FeedbackType.TIME,
                None,
                [Issue.AHEAD_OF_SCHEDULE],
                short / 1000 * 0.75,
                short,
                1,
                0.75,
                evidence={
                    "duration_ms": take.duration_ms,
                    "min_ms": plan.min_ms,
                    "short_ms": short,
                },
            )

    return [g for g in groups.values() if g.burden_s >= rc.min_burden_s]


# ── 미션 판정 ─────────────────────────────────────────────────────────────


def satisfies(value: float, operator: str, target: float) -> bool:
    match operator:
        case "LT":
            return value < target
        case "LTE":
            return value <= target
        case "GT":
            return value > target
        case "GTE":
            return value >= target
        case "EQ":
            return math.isclose(value, target, abs_tol=1e-9)
    return False


def miss_by(value: float, operator: str, target: float) -> float:
    match operator:
        case "LT" | "LTE":
            return max(0.0, value - target)
        case "GT" | "GTE":
            return max(0.0, target - value)
    return abs(value - target)


def mission_status(value: float, operator: str, target: float, tolerance: float) -> MissionStatus:
    if satisfies(value, operator, target):
        return MissionStatus.ACHIEVED
    if miss_by(value, operator, target) <= tolerance:
        return MissionStatus.PARTIAL
    return MissionStatus.FAILED


#: 미션 target 으로 판정할 수 있는 지표 → (Agg 에서 읽는 법, 데이터 덮개)
_SCOPE_METRICS = {
    "script_ratio": ("script_ratio", "gaze_coverage"),
    "cpm": ("cpm", "speech_coverage"),
    "voice_diff_db": ("voice_diff_db", "volume_coverage"),
    "filler_per_min": ("filler_per_min", "speech_coverage"),
}
SUPPORTED_MISSION_METRICS = frozenset(
    {*_SCOPE_METRICS, "slide_duration_ms", "duration_ms", "long_silence_count"}
)


def observe_metric(
    metric: str,
    slide_number: int | None,
    slides: dict[int, Agg],
    take: Agg,
    segs: list[Seg],
    cfg: CoachConfig,
) -> tuple[float | None, float | None, str | None]:
    """(관측값, 데이터 덮개, 판단 못 한 이유)."""
    if metric not in SUPPORTED_MISSION_METRICS:
        return None, None, "UNSUPPORTED_METRIC"
    if take.total_ms <= 0:
        return None, None, "NO_DATA"
    scope = take
    if slide_number is not None:
        if slide_number not in slides:
            return None, None, "NOT_REACHED"
        scope = slides[slide_number]

    if metric == "slide_duration_ms":
        if slide_number is None:
            return None, None, "UNSUPPORTED_METRIC"
        return float(scope.duration_ms), 1.0, None
    if metric == "duration_ms":
        return float(take.duration_ms), 1.0, None
    if metric == "long_silence_count":
        n = sum(
            1
            for s in segs
            if s.area == FeedbackType.PAUSE
            and is_reliable(s, cfg)
            and (slide_number is None or s.slide_number == slide_number)
        )
        return float(n), scope.audio_coverage, None

    attr, cover_attr = _SCOPE_METRICS[metric]
    value = getattr(scope, attr)
    coverage = getattr(scope, cover_attr)
    if value is None:
        return None, coverage, "NO_DATA"
    return float(value), coverage, None


def evaluate_missions(
    missions: list[Mission],
    slides: dict[int, Agg],
    take: Agg,
    segs: list[Seg],
    cfg: CoachConfig,
) -> list[MissionReview]:
    out: list[MissionReview] = []
    for m in missions:
        base: dict[str, Any] = {
            "mission_id": m.mission_id,
            "area": m.area,
            "slide_number": m.slide_number,
            "metric": m.target.metric if m.target else "",
            "operator": m.target.operator if m.target else None,
            "target": m.target.value if m.target else None,
        }
        if m.target is None:
            out.append(
                MissionReview(
                    **base,
                    status=MissionStatus.NOT_EVALUABLE,
                    achieved=False,
                    observed=None,
                    coverage=None,
                    reason="NO_TARGET",
                )
            )
            continue
        observed, coverage, reason = observe_metric(
            m.target.metric, m.slide_number, slides, take, segs, cfg
        )
        if reason is None and (coverage is None or coverage < cfg.review.min_coverage):
            reason = "LOW_DATA_COVERAGE"
        if reason is not None or observed is None:
            status = MissionStatus.NOT_EVALUABLE
        else:
            tol = cfg.review.partial_tolerance.get(m.target.metric, 0.0)
            status = mission_status(observed, m.target.operator, m.target.value, tol)
        out.append(
            MissionReview(
                **base,
                status=status,
                achieved=status == MissionStatus.ACHIEVED,
                observed=observed,
                coverage=coverage,
                reason=reason,
            )
        )
    return out


# ── 순위 · 기억 · 상태 · 다음 미션 ───────────────────────────────────────────


def _memory_matches(memory: Memory, ftype: FeedbackType, slide_number: int | None) -> bool:
    return any(
        r.area == ftype and (r.slide_number is None or r.slide_number == slide_number)
        for r in memory.recurring_issues
    )


def _mission_failed(
    missions: list[MissionReview], ftype: FeedbackType, slide_number: int | None
) -> bool:
    return any(
        m.area == ftype
        and m.status in (MissionStatus.FAILED, MissionStatus.PARTIAL)
        and (m.slide_number is None or m.slide_number == slide_number)
        for m in missions
    )


def assess(
    slides: dict[int, Agg],
    take: Agg,
    segs: list[Seg],
    plan: Plan | None,
    missions: list[Mission],
    memory: Memory,
    cfg: CoachConfig,
    *,
    outcome_of: dict[str, Outcome] | None = None,
    gave_up_ids: set[str] | None = None,
) -> Assessment:
    rc = cfg.review
    outcome_of = outcome_of or {}
    gave_up_ids = gave_up_ids or set()
    evaluable = evaluable_types(take, plan, cfg)
    raw = [i for i in collect_issues(slides, take, segs, plan, cfg) if evaluable[i.area]]
    mission_reviews = evaluate_missions(missions, slides, take, segs, cfg)

    reviews: list[IssueReview] = []
    for i in raw:
        recurring = _memory_matches(memory, i.area, i.slide_number)
        gave_up = any(iv in gave_up_ids for iv in i.intervention_ids)
        failed = _mission_failed(mission_reviews, i.area, i.slide_number)
        score = i.burden_s
        score *= rc.recurring_weight if recurring else 1.0
        score *= rc.gave_up_weight if gave_up else 1.0
        score *= rc.mission_failed_weight if failed else 1.0
        outs = [outcome_of.get(iv) for iv in i.intervention_ids]
        reviews.append(
            IssueReview(
                rank=0,
                area=i.area,
                slide_number=i.slide_number,
                issue_types=i.issue_types,
                burden_s=round(i.burden_s, 2),
                score=round(score, 2),
                duration_ms=i.duration_ms,
                segments=i.segments,
                peak_severity=round(i.peak_severity, 4),
                interventions=len(i.intervention_ids),
                effective=sum(o == Outcome.EFFECTIVE for o in outs),
                ineffective=sum(o == Outcome.INEFFECTIVE for o in outs),
                gave_up=gave_up,
                memory=MemoryLabel.RECURRING if recurring else MemoryLabel.NEW,
                mission_failed=failed,
                evidence=i.evidence,
            )
        )
    # 순위는 영역 전체 점수가 먼저다. 같은 영역이 여러 장에 나뉘어도(시선은 장마다 따로 잡힌다)
    # '다음에 먼저 고칠 영역'이 흔들리지 않게 — rank 1 은 1순위 영역에서 가장 큰 장
    type_score: dict[FeedbackType, float] = {}
    for r in reviews:
        type_score[r.area] = type_score.get(r.area, 0.0) + r.score
    reviews.sort(
        key=lambda r: (
            -type_score[r.area],
            TYPE_ORDER.index(r.area),
            -r.score,
            r.slide_number or 0,
        )
    )
    for n, r in enumerate(reviews, start=1):
        r.rank = n

    memory_reviews: list[MemoryReview] = []
    for item in memory.recurring_issues:
        hits = [
            r
            for r in reviews
            if r.area == item.area
            and (item.slide_number is None or r.slide_number == item.slide_number)
        ]
        reached = item.slide_number is None or item.slide_number in slides
        if hits:
            label = MemoryLabel.RECURRING
        elif evaluable[item.area] and reached:
            label = MemoryLabel.RESOLVED
        else:
            label = MemoryLabel.UNKNOWN
        memory_reviews.append(
            MemoryReview(
                area=item.area,
                slide_number=item.slide_number,
                label=label,
                burden_s=round(sum(r.burden_s for r in hits), 2),
            )
        )

    type_status = _type_status(reviews, mission_reviews, memory_reviews, evaluable, take, cfg)
    next_missions = _next_missions(reviews, type_status, slides, take, plan, cfg)
    return Assessment(
        reviews, mission_reviews, memory_reviews, type_status, next_missions, evaluable
    )


def _take_metric(ftype: FeedbackType, take: Agg) -> dict[str, Any]:
    return {
        FeedbackType.GAZE: {"script_ratio": take.script_ratio, "coverage": take.gaze_coverage},
        FeedbackType.SPEED: {"cpm": take.cpm, "coverage": take.speech_coverage},
        FeedbackType.VOLUME: {"voice_diff_db": take.voice_diff_db, "coverage": take.audio_coverage},
        FeedbackType.PAUSE: {"long_silence_ms": take.long_silence_ms},
        FeedbackType.FILLER: {"filler_per_min": take.filler_per_min},
        FeedbackType.CONTENT: {},
        FeedbackType.TIME: {"duration_ms": take.duration_ms},
    }[ftype]


def _type_status(
    issues: list[IssueReview],
    missions: list[MissionReview],
    memory: list[MemoryReview],
    evaluable: dict[FeedbackType, bool],
    take: Agg,
    cfg: CoachConfig,
) -> list[TypeStatusReview]:
    scores: dict[FeedbackType, float] = {}
    burdens: dict[FeedbackType, float] = {}
    for r in issues:
        scores[r.area] = scores.get(r.area, 0.0) + r.score
        burdens[r.area] = burdens.get(r.area, 0.0) + r.burden_s
    ranked = sorted(scores, key=lambda t: (-scores[t], TYPE_ORDER.index(t)))
    priority = set(ranked[: cfg.review.priority_types])

    out: list[TypeStatusReview] = []
    for ftype in TYPE_ORDER:
        mem = [m for m in memory if m.area == ftype]
        mem_label = None
        if mem:
            labels = {m.label for m in mem}
            mem_label = (
                MemoryLabel.RECURRING
                if MemoryLabel.RECURRING in labels
                else MemoryLabel.RESOLVED
                if MemoryLabel.RESOLVED in labels
                else MemoryLabel.UNKNOWN
            )
        achieved = any(m.area == ftype and m.status == MissionStatus.ACHIEVED for m in missions)
        if not evaluable[ftype]:
            status = TypeStatus.NOT_EVALUABLE
        elif ftype in priority:
            status = TypeStatus.PRIORITY
        elif mem_label == MemoryLabel.RESOLVED or (achieved and ftype not in scores):
            status = TypeStatus.IMPROVED
        elif ftype not in scores:
            status = TypeStatus.STRENGTH
        else:
            status = TypeStatus.STABLE
        out.append(
            TypeStatusReview(
                area=ftype,
                status=status,
                burden_s=round(burdens.get(ftype, 0.0), 2),
                memory=mem_label,
                evidence=_take_metric(ftype, take),
            )
        )
    return out


def _next_target(
    ftype: FeedbackType,
    slide_number: int | None,
    issue: IssueReview,
    scope: Agg,
    plan: Plan | None,
    cfg: CoachConfig,
) -> tuple[NextMissionTarget, float | None] | None:
    rc = cfg.review
    match ftype:
        case FeedbackType.GAZE:
            obs = scope.script_ratio
            value = rc.gaze_goal if obs is None else max(rc.gaze_goal, round(obs - rc.gaze_step, 2))
            return NextMissionTarget(metric="script_ratio", operator="LTE", value=value), obs
        case FeedbackType.SPEED:
            return NextMissionTarget(
                metric="cpm", operator="LTE", value=cfg.speech.fast_cpm
            ), scope.cpm
        case FeedbackType.VOLUME:
            return NextMissionTarget(
                metric="voice_diff_db", operator="GTE", value=cfg.voice.low_relative_db
            ), scope.voice_diff_db
        case FeedbackType.FILLER:
            obs = scope.filler_per_min
            value = rc.filler_goal if obs is None else max(rc.filler_goal, round(obs / 2, 1))
            return NextMissionTarget(metric="filler_per_min", operator="LTE", value=value), obs
        case FeedbackType.PAUSE:
            n = float(issue.segments)
            return NextMissionTarget(metric="long_silence_count", operator="LTE", value=0), n
        case FeedbackType.TIME:
            if slide_number is not None and scope.target_ms:
                value = round(scope.target_ms * rc.slide_time_margin)
                return NextMissionTarget(
                    metric="slide_duration_ms", operator="LTE", value=value
                ), float(scope.duration_ms)
            if Issue.AHEAD_OF_SCHEDULE in issue.issue_types and plan is not None and plan.min_ms:
                return NextMissionTarget(
                    metric="duration_ms", operator="GTE", value=plan.min_ms
                ), float(scope.duration_ms)
            if plan is not None and plan.target_ms:
                limit = plan.max_ms or plan.target_ms
                return NextMissionTarget(metric="duration_ms", operator="LTE", value=limit), float(
                    scope.duration_ms
                )
    return None


def _next_missions(
    issues: list[IssueReview],
    type_status: list[TypeStatusReview],
    slides: dict[int, Agg],
    take: Agg,
    plan: Plan | None,
    cfg: CoachConfig,
) -> list[NextMission]:
    rc = cfg.review
    status = {t.area: t.status for t in type_status}
    type_burden: dict[FeedbackType, float] = {}
    for r in issues:
        type_burden[r.area] = type_burden.get(r.area, 0.0) + r.burden_s

    out: list[NextMission] = []
    seen: set[FeedbackType] = set()
    for r in issues:
        if r.area in seen or status.get(r.area) == TypeStatus.NOT_EVALUABLE:
            continue
        seen.add(r.area)
        share = r.burden_s / type_burden[r.area] if type_burden[r.area] else 0.0
        slide_scoped = r.slide_number is not None and (
            r.area in (FeedbackType.TIME, FeedbackType.CONTENT) or share >= rc.slide_mission_share
        )
        slide_number = r.slide_number if slide_scoped else None
        scope = slides[slide_number] if slide_number is not None else take
        built = _next_target(r.area, slide_number, r, scope, plan, cfg)
        if built is None:
            continue
        target, observed = built
        reasons = []
        if r.rank == 1:
            reasons.append("TOP_BURDEN")
        if r.memory == MemoryLabel.RECURRING:
            reasons.append("RECURRING")
        if r.gave_up:
            reasons.append("GAVE_UP")
        if r.mission_failed:
            reasons.append("MISSION_FAILED")
        out.append(
            NextMission(
                priority=len(out) + 1,
                area=r.area,
                slide_number=slide_number,
                target=target,
                observed=observed,
                reason_codes=reasons,
                evidence={"burden_s": r.burden_s, "issue_types": [i.value for i in r.issue_types]},
            )
        )
        if len(out) >= rc.max_next_missions:
            break
    return out


# ══════════════════════════════════════════════════════════════════════════
# 조립
# ══════════════════════════════════════════════════════════════════════════


def build_review_evidence(
    take_id: str,
    events: list[Any],
    *,
    plan: Plan | dict[str, Any] | None = None,
    missions: list[Mission] | list[dict[str, Any]] | None = None,
    memory: Memory | dict[str, Any] | None = None,
    config: CoachConfig | None = None,
) -> CoachReviewEvidence:
    cfg = config or DEFAULT_CONFIG
    plan_m = Plan.model_validate(plan) if isinstance(plan, dict) else plan
    missions_m = [Mission.model_validate(m) if isinstance(m, dict) else m for m in missions or []]
    memory_m = Memory.model_validate(memory) if isinstance(memory, dict) else (memory or Memory())
    parsed = _EVENTS.validate_python(
        [e.model_dump(mode="json") if hasattr(e, "model_dump") else e for e in events]
    )

    interventions = [e for e in parsed if isinstance(e, InterventionEvent)]
    outcomes = {e.intervention_id: e for e in parsed if isinstance(e, OutcomeEvent)}
    strategies = [e for e in parsed if isinstance(e, StrategyEvent)]
    episodes = [e for e in parsed if isinstance(e, EpisodeEvent)]
    suppressed = [e for e in parsed if isinstance(e, SuppressedEvent)]
    slide_events = [e for e in parsed if isinstance(e, SlideEvent)]
    gave_up_ids = {s.intervention_id for s in strategies if s.change == StrategyChange.GAVE_UP}

    iv_reviews = [_intervention_review(iv, outcomes.get(iv.event_id)) for iv in interventions]
    outcome_of = {r.intervention_id: r.outcome for r in iv_reviews}

    slides, take = aggregate_slides(slide_events)
    segs = merge_segments(segments_from_episodes(episodes, cfg), cfg)
    seg_reviews = [_segment(s, outcome_of, gave_up_ids, cfg) for s in segs]
    result = assess(
        slides,
        take,
        segs,
        plan_m,
        missions_m,
        memory_m,
        cfg,
        outcome_of=outcome_of,
        gave_up_ids=gave_up_ids,
    )

    corrections = [r for r in iv_reviews if r.instruction != Instruction.CONTINUE]
    effective = sum(r.outcome == Outcome.EFFECTIVE for r in corrections)
    ineffective = sum(r.outcome == Outcome.INEFFECTIVE for r in corrections)
    measured = effective + ineffective
    suppressed_by_reason: Counter[str] = Counter()
    for s in suppressed:
        suppressed_by_reason.update(s.reasons)
    real_segments = [s for s in seg_reviews if s.hint != SegmentHint.UNRELIABLE]

    return CoachReviewEvidence(
        policy_version=POLICY_VERSION,
        config_hash=cfg.config_hash(),
        take_id=take_id,
        summary=CoachingSummary(
            interventions=len(iv_reviews),
            praises=len(iv_reviews) - len(corrections),
            effective=effective,
            ineffective=ineffective,
            not_measured=sum(r.outcome == Outcome.NOT_MEASURED for r in corrections),
            effective_rate=round(effective / measured, 4) if measured else None,
            gave_up=len(gave_up_ids),
            episodes=len(real_segments),
            episodes_unaddressed=sum(s.hint == SegmentHint.UNADDRESSED for s in real_segments),
            suppressed_by_reason=dict(sorted(suppressed_by_reason.items())),
        ),
        data_quality=DataQuality(
            duration_ms=take.duration_ms,
            gaze_coverage=take.gaze_coverage,
            speech_coverage=take.speech_coverage,
            audio_coverage=take.audio_coverage,
            unreliable_segments=len(seg_reviews) - len(real_segments),
            evaluable={t.value: v for t, v in result.evaluable.items()},
        ),
        type_status=result.type_status,
        issues=result.issues,
        next_missions=result.next_missions,
        mission_results=result.missions,
        memory_check=result.memory,
        strengths=_strengths(result, iv_reviews, take, plan_m),
        slides=[_slide_review(s, result.issues) for _, s in sorted(slides.items())],
        segments=seg_reviews,
        interventions=iv_reviews,
        strategy_changes=[
            StrategyReview(
                t_ms=s.t_ms,
                area=s.area,
                issue_type=s.issue_type,
                slide_number=s.slide_number,
                change=s.change,
                from_instruction=s.from_instruction,
                to_instruction=s.to_instruction,
            )
            for s in strategies
        ],
        by_type=_by_type(corrections, real_segments),
    )


def _intervention_review(iv: InterventionEvent, oc: OutcomeEvent | None) -> InterventionReview:
    return InterventionReview(
        intervention_id=iv.event_id,
        t_ms=iv.t_ms,
        slide_number=iv.slide_number,
        area=iv.area,
        instruction=iv.instruction,
        message=iv.message,
        reason_codes=iv.reason_codes,
        evidence=iv.evidence,
        outcome=oc.outcome if oc is not None else Outcome.NOT_MEASURED,
        outcome_metric=oc.metric if oc is not None else None,
        before=oc.before if oc is not None else None,
        after=oc.after if oc is not None else None,
    )


def _segment(
    seg: Seg, outcome_of: dict[str, Outcome], gave_up_ids: set[str], cfg: CoachConfig
) -> SegmentReview:
    outcomes = [outcome_of.get(i, Outcome.NOT_MEASURED) for i in seg.intervention_ids]
    if cfg.review.exclude_unreliable and not is_reliable(seg, cfg):
        hint = SegmentHint.UNRELIABLE
    elif not seg.intervention_ids:
        hint = SegmentHint.UNADDRESSED
    elif any(i in gave_up_ids for i in seg.intervention_ids):
        # 여러 방법을 써도 안 바뀐 구간 — 리뷰가 '이 장은 대본 없이 어렵다' 같은 미션으로 바꿀 곳
        hint = SegmentHint.GAVE_UP
    elif Outcome.EFFECTIVE in outcomes:
        hint = SegmentHint.COACHED_EFFECTIVE
    elif Outcome.INEFFECTIVE in outcomes:
        hint = SegmentHint.COACHED_INEFFECTIVE
    else:
        hint = SegmentHint.COACHED_NOT_MEASURED
    return SegmentReview(
        candidate_id=seg.candidate_ids[0],
        candidate_ids=seg.candidate_ids,
        slide_number=seg.slide_number,
        start_ms=seg.start_ms,
        end_ms=seg.end_ms,
        onset_ms=seg.onset_ms,
        offset_ms=seg.offset_ms,
        area=seg.area,
        issue_type=seg.issue_types[0],
        issue_types=seg.issue_types,
        peak_severity=seg.peak_severity,
        mean_severity=round(seg.mean_severity or seg.peak_severity, 4),
        reliability=seg.reliability,
        hint=hint,
        intervention_ids=seg.intervention_ids,
        suppressed_reasons=seg.suppressed_reasons,
        evidence=seg.evidence,
    )


def _slide_review(s: Agg, issues: list[IssueReview]) -> SlideReview:
    assert s.slide_number is not None
    return SlideReview(
        slide_number=s.slide_number,
        visits=s.visits,
        start_ms=s.start_ms,
        duration_ms=s.duration_ms,
        target_ms=s.target_ms,
        over_ms=s.over_ms,
        script_ratio=s.script_ratio,
        cpm=s.cpm,
        voice_diff_db=s.voice_diff_db,
        filler_count=s.filler_count,
        filler_per_min=s.filler_per_min,
        long_silence_ms=s.long_silence_ms,
        gaze_coverage=s.gaze_coverage,
        speech_coverage=s.speech_coverage,
        audio_coverage=s.audio_coverage,
        areas=sorted(
            {r.area for r in issues if r.slide_number == s.slide_number}, key=TYPE_ORDER.index
        ),
    )


def _strengths(
    result: Assessment, ivs: list[InterventionReview], take: Agg, plan: Plan | None
) -> list[StrengthReview]:
    out: list[StrengthReview] = []
    for t in result.type_status:
        if t.status == TypeStatus.STRENGTH:
            out.append(StrengthReview(kind=StrengthKind.CLEAN, area=t.area, evidence=t.evidence))
    for m in result.memory:
        if m.label == MemoryLabel.RESOLVED:
            out.append(
                StrengthReview(
                    kind=StrengthKind.RESOLVED_RECURRING,
                    area=m.area,
                    slide_number=m.slide_number,
                    evidence={"memory": "RECURRING_IN_PREVIOUS_TAKE", "burden_s": m.burden_s},
                )
            )
    for m in result.missions:
        if m.status == MissionStatus.ACHIEVED:
            out.append(
                StrengthReview(
                    kind=StrengthKind.MISSION_ACHIEVED,
                    area=m.area,
                    slide_number=m.slide_number,
                    evidence={
                        "mission_id": m.mission_id,
                        "metric": m.metric,
                        "target": m.target,
                        "observed": m.observed,
                    },
                )
            )
    seen: set[FeedbackType] = set()
    for iv in ivs:
        if (
            iv.outcome == Outcome.EFFECTIVE
            and iv.instruction != Instruction.CONTINUE
            and iv.area not in seen
        ):
            seen.add(iv.area)
            out.append(
                StrengthReview(
                    kind=StrengthKind.RESPONDED_TO_COACHING,
                    area=iv.area,
                    slide_number=iv.slide_number,
                    evidence={
                        "intervention_id": iv.intervention_id,
                        "metric": iv.outcome_metric,
                        "before": iv.before,
                        "after": iv.after,
                    },
                )
            )
    if plan is not None and plan.target_ms and take.duration_ms > 0:
        lo = plan.min_ms or round(plan.target_ms * 0.85)
        hi = plan.max_ms or round(plan.target_ms * 1.05)
        if lo <= take.duration_ms <= hi:
            out.append(
                StrengthReview(
                    kind=StrengthKind.ON_TIME,
                    area=FeedbackType.TIME,
                    evidence={"duration_ms": take.duration_ms, "min_ms": lo, "max_ms": hi},
                )
            )
    return out


def _by_type(
    corrections: list[InterventionReview], segments: list[SegmentReview]
) -> list[TypeSummary]:
    out: list[TypeSummary] = []
    for ftype in FeedbackType:
        ivs = [r for r in corrections if r.area == ftype]
        segs = [s for s in segments if s.area == ftype]
        if not ivs and not segs:
            continue
        out.append(
            TypeSummary(
                area=ftype,
                interventions=len(ivs),
                effective=sum(r.outcome == Outcome.EFFECTIVE for r in ivs),
                ineffective=sum(r.outcome == Outcome.INEFFECTIVE for r in ivs),
                not_measured=sum(r.outcome == Outcome.NOT_MEASURED for r in ivs),
                episodes=len(segs),
                episodes_unaddressed=sum(s.hint == SegmentHint.UNADDRESSED for s in segs),
            )
        )
    return out
