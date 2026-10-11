"""시간 — 계획 대비 진행을 판정한다.

계획이 있어야 하는 판정이라 코치 모듈 안에 두되, 결과는 다른 판정 모듈과 같은
1초 판정 결과(#158)로 낸다. 기준값은 `config.TimingConfig` 에만 있다.

핵심 값은 필요 속도 비율 r 입니다.

    현재 장 진행도  = 이 장에서 말한 글자 수 ÷ 이 장 대본 글자 수
                     (STT 를 믿을 수 없으면 대신: 이 장에 머문 시간 ÷ 이 장 목표 시간)
    남은 내용 시간  = 이 장 목표 × (1 − 진행도) + 뒤 장들의 목표 합
    r              = 남은 내용 시간 ÷ 남은 시간          1 보다 크면 늦는 중
    예상 종료      = 지금 + 남은 내용 시간 × (경과 ÷ 소화한 계획 시간)   허용 최소보다 이르면 빠름

호출하는 쪽이 장별 누적(말한 글자 수 · STT 를 믿은 시간 · 머문 시간)을 들고 있다가 매번 넘긴다.
이 모듈은 상태를 갖지 않는다.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .config import DEFAULT_CONFIG, TimingConfig, criteria_version
from .schemas import IssueCriteria, JudgmentIssue, JudgmentResult, Plan, TallyItem
from .version import TIMING_VERSION
from .vocab import FeedbackType, Issue

_TICK_MS = 1_000
_HIGHER = "HIGHER_IS_WORSE"
_LOWER = "LOWER_IS_WORSE"


class _In(BaseModel):
    model_config = ConfigDict(extra="ignore")


class SlideRef(_In):
    """지금 보여 주는 장."""

    number: int
    started_ms: int


class PaceRef(_In):
    """속도 판정 결과 중 시간 판정이 쓰는 값."""

    cpm: float | None = None
    measurable: bool = False
    fast_threshold: float | None = None


class TimingInput(_In):
    t_ms: int
    #: 여기까지는 이미 셌다 (이전 결과의 counted_until_ms)
    since_ms: int = 0
    plan: Plan
    slide: SlideRef | None = None
    #: 장 번호(문자열)별 누적: 말한 글자 수 · STT 를 믿은 시간 · 머문 시간.
    #: 머문 시간은 이번 호출에서 더할 몫(since_ms 이후)을 뺀 값이다. STT 를 믿은 시간과 머문 시간은
    #: 같은 조각(1초씩, 장 시작에서 끊음)으로 센 값이어야 한다 — 그래야 끊김이 없을 때 둘이 같다
    slide_chars: dict[str, int] = Field(default_factory=dict)
    slide_stt_ok_ms: dict[str, int] = Field(default_factory=dict)
    slide_dwell_ms: dict[str, int] = Field(default_factory=dict)
    pace: PaceRef | None = None


def severity(value: float, threshold: float, bad: float, direction: str) -> float:
    """기준값에서 0.5, 아주 나쁨 값에서 1.0, 사이는 직선, 1.0 에서 자른다 (공통 규칙)."""
    if bad == threshold:
        return 1.0
    if direction == _HIGHER:
        frac = (value - threshold) / (bad - threshold)
    else:
        frac = (threshold - value) / (threshold - bad)
    return round(0.5 + 0.5 * min(1.0, max(0.0, frac)), 4)


def criteria(config: TimingConfig = DEFAULT_CONFIG.timing) -> dict[str, IssueCriteria]:
    """문제별 판정 기준. judge 가 쓰는 값과 같다."""
    return {
        Issue.TIME_OVER: IssueCriteria(
            metric="elapsed_ratio",
            direction=_HIGHER,
            threshold=1.0,
            bad=config.time_over_bad_ratio,
        ),
        Issue.FINAL_MINUTE: IssueCriteria(
            metric="remaining_ms",
            direction=_LOWER,
            threshold=config.final_minute_ms,
            bad=config.final_minute_bad_ms,
        ),
        Issue.BEHIND_SCHEDULE: IssueCriteria(
            metric="required_ratio",
            direction=_HIGHER,
            threshold=config.behind_ratio,
            bad=config.behind_ratio_bad,
        ),
        Issue.SLIDE_OVER: IssueCriteria(
            metric="slide_time_ratio",
            direction=_HIGHER,
            threshold=config.slide_over_factor,
            bad=config.slide_over_bad_factor,
        ),
        Issue.AHEAD_OF_SCHEDULE: IssueCriteria(
            metric="projected_end_ratio",
            direction=_LOWER,
            threshold=1.0,
            bad=config.ahead_bad_ratio,
        ),
    }


def summarize(
    tally: dict[str, dict[str, float]], config: TimingConfig = DEFAULT_CONFIG.timing
) -> dict[str, dict[str, float | None]]:
    """합친 집계(elapsed_ms · planned_ms)를 시간 길이와 계획 대비 측정 비율로 바꾼다."""
    time = tally.get("TIME", {})
    elapsed = time.get("elapsed_ms", 0)
    planned = time.get("planned_ms", 0)
    return {
        "TIME": {
            "duration_ms": elapsed,
            "measured_ratio": round(planned / elapsed, 4) if elapsed > 0 else None,
        }
    }


def judge(
    inputs: TimingInput | dict[str, Any],
    t_ms: int,
    config: TimingConfig = DEFAULT_CONFIG.timing,
) -> list[JudgmentResult]:
    """t_ms 시점의 시간 판정. 결과는 항상 하나다."""
    inp = inputs if isinstance(inputs, TimingInput) else TimingInput.model_validate(inputs)
    plan = inp.plan
    t = t_ms
    target = plan.target_ms
    common: dict[str, Any] = {
        "evaluator": "timing",
        "area": FeedbackType.TIME,
        "t_ms": t,
        "counted_until_ms": max(inp.since_ms, t),
        "criteria_version": criteria_version(TIMING_VERSION, config),
        "tally": _tally(inp.since_ms, t, inp.slide, target is not None),
    }
    if target is None:
        # 목표 시간이 없으면 잴 것이 없다
        names = [
            "required_ratio",
            "projected_end_ms",
            "required_cpm",
            "slide_elapsed_ms",
            "slide_expected_ms",
            "remaining_ms",
            "elapsed_ratio",
            "projected_end_ratio",
            "slide_time_ratio",
        ]
        unknown = JudgmentResult(
            **common, measurable=False, state="UNKNOWN", metrics=dict.fromkeys(names)
        )
        return [unknown]

    # 장 목표의 합이 전체 목표와 다르면 비율대로 맞춘다
    slides = sorted(plan.slides, key=lambda s: s.slide_number)
    targets = {s.slide_number: float(s.target_ms) for s in slides}
    total = sum(targets.values())
    if total > 0 and total != target:
        targets = {n: v * target / total for n, v in targets.items()}

    limit = plan.max_ms or target
    remaining = target - t
    early_limit = plan.min_ms if plan.min_ms is not None else round(target * config.early_end_ratio)

    number = inp.slide.number if inp.slide is not None else None
    current = number if number in targets else None
    r: float | None = None
    projected_end: int | None = None
    remaining_slides: int | None = None
    remaining_content: float | None = None
    dwell_now: int | None = None
    cur_target = 0.0
    slide_time_ratio: float | None = None
    confidence = config.progress_confidence_time
    if current is not None and inp.slide is not None:
        key = str(current)
        cur_target = targets[current]
        script_chars = next(s.script_chars for s in slides if s.slide_number == current)
        dwell_done = inp.slide_dwell_ms.get(key, 0)
        dwell_now = dwell_done + max(0, t - max(inp.since_ms, inp.slide.started_ms))
        stt_ok = inp.slide_stt_ok_ms.get(key, 0)
        # 말한 글자 수는 그 장에 머무는 동안 STT 를 계속 믿을 수 있었을 때만 진행도로 쓴다
        if script_chars > 0 and stt_ok >= dwell_done:
            progress = min(1.0, inp.slide_chars.get(key, 0) / script_chars)
            confidence = config.progress_confidence_chars
        elif cur_target > 0:
            progress = min(1.0, dwell_now / cur_target)
        else:
            progress = 1.0
        earlier = sum(v for n, v in targets.items() if n < current)
        later = [v for n, v in targets.items() if n > current]
        remaining_content = cur_target * (1.0 - progress) + sum(later)
        remaining_slides = 1 + len(later)
        if remaining > 0:
            r = remaining_content / remaining
        # 계획 ms 를 실제 ms 당 얼마나 소화했는지로 끝나는 시각을 어림한다
        covered = earlier + cur_target * progress
        if t > 0 and covered > 0 and remaining > 0:
            projected_end = round(t + remaining_content * t / covered)
        if cur_target > 0:
            slide_time_ratio = dwell_now / cur_target

    pace = inp.pace
    raw_cpm: float | None = None
    if pace is not None and pace.measurable and pace.cpm is not None and r is not None:
        raw_cpm = pace.cpm * r
    required_cpm = round(raw_cpm, 1) if raw_cpm is not None else None
    elapsed_ratio = round(t / limit, 4)
    projected_end_ratio = (
        _round(projected_end / early_limit)
        if projected_end is not None and early_limit > 0
        else None
    )
    required_ratio = _round(r)
    slide_ratio = _round(slide_time_ratio)
    metrics: dict[str, float | None] = {
        "required_ratio": required_ratio,
        "projected_end_ms": projected_end,
        "required_cpm": required_cpm,
        "slide_elapsed_ms": dwell_now,
        "slide_expected_ms": round(cur_target) if current is not None else None,
        "remaining_ms": remaining,
        "elapsed_ratio": elapsed_ratio,
        "projected_end_ratio": projected_end_ratio,
        "slide_time_ratio": slide_ratio,
    }

    behind = r is not None and r >= config.behind_ratio
    ahead = (
        projected_end is not None
        and t >= config.ahead_min_elapsed_ratio * target
        and projected_end < early_limit
    )
    if t > limit:
        state = "OVER"
    elif remaining <= 0:
        # 목표 시간은 지났지만 허용 최대 안 — 이미 끝냈어야 하니 늦은 것이다
        state = "BEHIND"
    elif r is None:
        # 장별 계획이 없어 진행을 모르면 늦다고 하지 않는다
        state = "ON_TRACK"
    elif behind:
        state = "BEHIND"
    elif ahead:
        state = "AHEAD"
    else:
        state = "ON_TRACK"

    crit = criteria(config)
    issues: list[JudgmentIssue] = []

    def add(issue: Issue, value: float, conf: float, evidence: dict[str, Any]) -> None:
        c = crit[issue]
        issues.append(
            JudgmentIssue(
                issue_type=issue,
                area=FeedbackType.TIME,
                severity=severity(value, c.threshold, c.bad, c.direction),
                confidence=conf,
                persistence_sec=0.0,
                threshold=c.threshold,
                bad=c.bad,
                evidence=evidence,
            )
        )

    if t > limit:
        add(
            Issue.TIME_OVER,
            elapsed_ratio,
            1.0,
            {
                "elapsed_ms": t,
                "limit_ms": limit,
                "over_ms": t - limit,
                "elapsed_ratio": elapsed_ratio,
            },
        )
    # 계획대로 가는 발표자에게 '결론으로'는 틀린 말이다 — 장별 계획이 없거나 실제로 늦을 때만 띄운다
    # 목표 시간이 지나도 허용 최대 전까지는 마무리 구간이다 (그 뒤는 TIME_OVER 가 맡는다)
    if (
        remaining <= config.final_minute_ms
        and t <= limit
        and (remaining_slides is None or remaining_slides >= config.final_minute_min_slides)
        and (r is None or behind)
    ):
        add(
            Issue.FINAL_MINUTE,
            remaining,
            1.0,
            {"remaining_ms": remaining, "remaining_slides": remaining_slides},
        )
    if r is not None and behind and remaining_content is not None:
        if raw_cpm is not None and pace is not None and pace.fast_threshold is not None:
            speed_limit_exceeded = raw_cpm > pace.fast_threshold
        else:
            speed_limit_exceeded = r > config.condense_ratio_without_pace
        add(
            Issue.BEHIND_SCHEDULE,
            required_ratio or 0.0,
            confidence,
            {
                "required_ratio": required_ratio,
                "projected_end_ms": projected_end,
                "required_cpm": required_cpm,
                "speed_limit_exceeded": speed_limit_exceeded,
                "remaining_ms": remaining,
                "remaining_slides": remaining_slides,
                "remaining_content_ms": round(remaining_content),
            },
        )
    # 마지막 장이나 목표 시간이 지난 뒤에는 '다음 장으로'가 말이 안 된다
    # (그때는 FINAL_MINUTE / TIME_OVER 가 맡는다)
    if (
        dwell_now is not None
        and slide_ratio is not None
        and slide_time_ratio is not None
        and remaining > 0
        and remaining_slides is not None
        and remaining_slides >= 2
        and slide_time_ratio > config.slide_over_factor
    ):
        add(
            Issue.SLIDE_OVER,
            slide_ratio,
            1.0,
            {
                "slide_elapsed_ms": dwell_now,
                "slide_expected_ms": round(cur_target),
                "over_ms": round(dwell_now - cur_target),
                "slide_time_ratio": slide_ratio,
            },
        )
    # '빠름'은 비율 r 이 아니라 '지금 속도면 허용 최소보다 일찍 끝나나'로 본다
    if ahead and projected_end_ratio is not None:
        add(
            Issue.AHEAD_OF_SCHEDULE,
            projected_end_ratio,
            confidence,
            {
                "projected_end_ms": projected_end,
                "early_limit_ms": early_limit,
                "target_ms": target,
                "projected_end_ratio": projected_end_ratio,
            },
        )

    return [JudgmentResult(**common, measurable=True, state=state, metrics=metrics, issues=issues)]


def _round(value: float | None) -> float | None:
    return round(value, 4) if value is not None else None


def _tally(since_ms: int, t: int, slide: SlideRef | None, planned: bool) -> list[TallyItem]:
    """[since_ms, t) 를 1초 이하 조각으로 나눈다. 장이 바뀐 시각에서도 자른다."""
    pieces: list[TallyItem] = []
    pos = since_ms
    while pos < t:
        end = min(pos + _TICK_MS, t)
        if slide is not None and pos < slide.started_ms < end:
            end = slide.started_ms
        length = end - pos
        values = {"elapsed_ms": length, "planned_ms": length if planned else 0}
        pieces.append(TallyItem(t_ms=pos, values=values))
        pos = end
    return pieces
