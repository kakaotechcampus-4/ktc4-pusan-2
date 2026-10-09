"""Take 결과의 목록 — 문제 구간 · 개입 · 포기. 이벤트에서 만든다.

받은 이벤트(BE 가 쌓은 것)와 이번 finalize 가 만든 이벤트를 event_id 로 거르며 합친 뒤
(`merge_events`), 그 목록에서 세 가지를 만든다. 순수 함수라 시계 · 파일을 쓰지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .config import TakeResultConfig
from .schemas import (
    CoachEvent,
    EpisodeEvent,
    GaveUp,
    InterventionEvent,
    IssueCriteria,
    OutcomeEvent,
    ProblemSegment,
    StrategyEvent,
    TakeIntervention,
)
from .vocab import FeedbackType, Issue, StrategyChange


def merge_events(received: list[CoachEvent], new: list[CoachEvent]) -> list[CoachEvent]:
    """받은 이벤트 뒤에 새 이벤트를 붙이되, 같은 event_id 는 처음 것 하나만 남긴다."""
    seen: set[str] = set()
    out: list[CoachEvent] = []
    for event in [*received, *new]:
        if event.event_id in seen:
            continue
        seen.add(event.event_id)
        out.append(event)
    return out


# ── 문제 구간 ─────────────────────────────────────────────────────────────


@dataclass
class _Part:
    """합치는 중인 문제 구간."""

    area: FeedbackType
    issue_type: Issue
    slide_number: int | None
    start_ms: int
    end_ms: int
    reliable: bool
    #: 평균 심각도 가중 합 · 가중치(믿을 수 있던 + 없던 시간)
    severity_ms: float = 0.0
    weight_ms: float = 0.0
    coached: bool = False
    #: 가중치가 0 인 조각만 있을 때의 대비 값
    fallback: list[float] = field(default_factory=list)

    @property
    def mean_severity(self) -> float:
        if self.weight_ms > 0:
            return self.severity_ms / self.weight_ms
        return sum(self.fallback) / len(self.fallback) if self.fallback else 0.0


def problem_segments(
    events: list[CoachEvent],
    cfg: TakeResultConfig,
    criteria: dict[str, dict[str, IssueCriteria]],
    duration_ms: int,
    slide_spans: dict[str, list[int]],
    tick_ms: int,
) -> list[ProblemSegment]:
    """EPISODE 이벤트 → 문제 구간.

    EPISODE 의 start_ms · end_ms 는 문제를 처음 · 마지막으로 본 판정 시각(그 1초가 끝난 시각)이라,
    마지막으로 본 1초를 덮도록 끝에 tick_ms 를 더해 본 시간으로 쓴다.

    ① 신뢰도: 믿을 수 없던 시간 비율이 1 - min_reliability 를 넘으면 reliable=false.
    ② 합치기: 같은 문제 · 같은 장 · 같은 신뢰도이고 간격이 merge_gap_ms 이하면 하나로.
       평균 심각도는 각 조각이 지켜본 시간(믿을 수 있던 + 없던 시간)으로 가중한다 — 에피소드의
       평균 심각도를 낼 때 쓴 가중치와 같아서, 합쳐도 구간 전체의 평균이 된다.
    ③ 짧은 잡음: 합친 구간이 min_segment_ms 보다 짧고 말을 건 적이 없으면 뺀다.
    ④ 보정: 시작 · 끝을 그 문제의 판정 지연만큼 앞으로 당기고(기준이 없으면 그대로) Take 와
       그 장이 보이던 시간 안으로 자른다. 자른 뒤 길이가 0 이하면 뺀다.
    """
    lags = {key: c for module in criteria.values() for key, c in module.items()}
    groups: dict[tuple[Issue, int | None, bool], list[_Part]] = {}
    episodes = sorted(
        (e for e in events if isinstance(e, EpisodeEvent)), key=lambda e: (e.start_ms, e.end_ms)
    )
    for ep in episodes:
        ep_end = ep.end_ms + tick_ms
        seen = ep.reliable_ms + ep.unreliable_ms
        reliable = not (seen > 0 and ep.unreliable_ms / seen > 1 - cfg.min_reliability)
        key = (ep.issue_type, ep.slide_number, reliable)
        parts = groups.setdefault(key, [])
        last = parts[-1] if parts else None
        if last is None or ep.start_ms - last.end_ms > cfg.merge_gap_ms:
            last = _Part(ep.area, ep.issue_type, ep.slide_number, ep.start_ms, ep_end, reliable)
            parts.append(last)
        last.end_ms = max(last.end_ms, ep_end)
        last.severity_ms += ep.mean_severity * seen
        last.weight_ms += seen
        last.fallback.append(ep.mean_severity)
        last.coached = last.coached or bool(ep.intervention_ids)

    out: list[ProblemSegment] = []
    for parts in groups.values():
        for part in parts:
            if not part.coached and part.end_ms - part.start_ms < cfg.min_segment_ms:
                continue
            crit = lags.get(part.issue_type.value)
            onset = _lag(crit, "onset_lag_ms")
            offset = _lag(crit, "offset_lag_ms")
            lo, hi = 0, duration_ms
            span = None
            if part.slide_number is not None:
                span = slide_spans.get(str(part.slide_number))
            if span is not None:
                lo, hi = max(lo, span[0]), min(hi, span[1])
            start = max(lo, part.start_ms - onset)
            end = min(hi, part.end_ms - offset)
            if end - start <= 0:
                continue
            out.append(
                ProblemSegment(
                    area=part.area,
                    issue_type=part.issue_type,
                    slide_number=part.slide_number,
                    start_ms=start,
                    end_ms=end,
                    mean_severity=round(part.mean_severity, 4),
                    reliable=part.reliable,
                    coached=part.coached,
                )
            )
    return sorted(out, key=lambda s: (s.start_ms, s.issue_type.value))


def _lag(criteria: IssueCriteria | dict[str, Any] | None, name: str) -> int:
    if criteria is None:
        return 0
    if isinstance(criteria, dict):
        return int(criteria.get(name, 0) or 0)
    return int(getattr(criteria, name, 0) or 0)


# ── 개입 · 포기 ───────────────────────────────────────────────────────────


def interventions(events: list[CoachEvent]) -> list[TakeIntervention]:
    """INTERVENTION 마다 같은 intervention_id 의 OUTCOME 을 붙인다(없으면 outcome 은 null)."""
    outcomes = {e.intervention_id: e for e in events if isinstance(e, OutcomeEvent)}
    out: list[TakeIntervention] = []
    for e in sorted((e for e in events if isinstance(e, InterventionEvent)), key=lambda e: e.t_ms):
        oc = outcomes.get(e.intervention_id)
        out.append(
            TakeIntervention(
                intervention_id=e.intervention_id,
                t_ms=e.t_ms,
                area=e.area,
                issue_type=e.issue_type,
                instruction=e.instruction,
                message=e.message,
                outcome=oc.outcome if oc else None,
                metric=oc.metric if oc else None,
                before=oc.before if oc else None,
                after=oc.after if oc else None,
            )
        )
    return out


def gave_up(events: list[CoachEvent]) -> list[GaveUp]:
    """방법을 다 써서 그만둔 문제(STRATEGY GAVE_UP). 같은 (문제, 장) 은 한 번만, 시간 순."""
    out: list[GaveUp] = []
    seen: set[tuple[Issue, int | None]] = set()
    for e in sorted((e for e in events if isinstance(e, StrategyEvent)), key=lambda e: e.t_ms):
        key = (e.issue_type, e.slide_number)
        if e.change != StrategyChange.GAVE_UP or key in seen:
            continue
        seen.add(key)
        out.append(GaveUp(issue_type=e.issue_type, slide_number=e.slide_number))
    return out
