"""시간 — 계획(대본 분석이 준 장별 목표 시간) 대비 지금 어디쯤인지 본다.

핵심 값은 필요 속도 비율 r 입니다.

    현재 장 진행도  = 이 장에서 말한 글자 수 ÷ 이 장 대본 글자 수
                     (STT 가 나쁘면 대신: 이 장에 머문 시간 ÷ 이 장 목표 시간)
    남은 내용 시간  = 이 장 목표 × (1 − 진행도) + 뒤 장들의 목표 합
    r              = 남은 내용 시간 ÷ 남은 시간          1 보다 크면 늦는 중
    예상 종료      = 지금 + 남은 내용 시간 × (경과 ÷ 소화한 계획 시간)   허용 최소보다 이르면 빠름

'1분 남음'처럼 남은 시간만 보면 3장째인지 9장째인지 구분하지 못합니다. r 은 둘을 가릅니다.
"""

from __future__ import annotations

from ..tick import Detection, Tick, ramp
from ..vocab import Issue, Schedule


def evaluate(tick: Tick) -> None:
    plan = tick.req.plan
    cfg = tick.cfg.timing
    tick.metrics["schedule"] = Schedule.UNKNOWN
    if plan.target_ms is None:
        return

    t = tick.t
    target = plan.target_ms
    limit = plan.max_ms or target
    remaining = target - t
    tick.metrics["remaining_ms"] = remaining

    timing = tick.req.current.timing
    slide_elapsed = timing.slide_elapsed_ms if timing else None
    slides = sorted(plan.slides, key=lambda s: s.slide_number)
    current = tick.slide_plan(tick.slide_number)

    r: float | None = None
    remaining_content: float | None = None
    remaining_slides: int | None = None
    projected_end: int | None = None
    confidence = cfg.progress_confidence_time
    early_limit = plan.min_ms if plan.min_ms is not None else round(target * cfg.early_end_ratio)

    if current is not None:
        said = tick.state.slide_chars.get(str(current.slide_number), 0)
        heard_whole_slide = current.slide_number not in tick.state.slides_unheard
        if tick.stt_ok and current.script_chars > 0 and heard_whole_slide:
            progress = min(1.0, said / current.script_chars)
            confidence = cfg.progress_confidence_chars
        elif slide_elapsed is not None:
            progress = min(1.0, slide_elapsed / current.target_ms) if current.target_ms > 0 else 1.0
        else:
            progress = 0.0
        earlier = [s for s in slides if s.slide_number < current.slide_number]
        later = [s for s in slides if s.slide_number > current.slide_number]
        remaining_content = current.target_ms * (1.0 - progress) + sum(s.target_ms for s in later)
        remaining_slides = 1 + len(later)
        if remaining > 0:
            r = remaining_content / remaining
        # 지금까지의 실제 속도로 끝까지 가면 언제 끝나나.
        # 계획 ms 를 실제 ms 당 얼마나 소화했는지로 잰다
        covered = sum(s.target_ms for s in earlier) + current.target_ms * progress
        if t > 0 and covered > 0:
            projected_end = round(t + remaining_content * t / covered)

        projected = slide_elapsed
        if slide_elapsed is not None and progress >= cfg.projection_min_progress:
            projected = round(slide_elapsed / progress)
        tick.metrics.update(
            {
                "slide_progress": round(progress, 4),
                "slide_elapsed_ms": slide_elapsed,
                "slide_target_ms": current.target_ms,
                # 미션 target 의 이름. 지금 속도면 이 장이 몇 ms 걸릴지
                "slide_duration_ms": projected,
                "remaining_content_ms": round(remaining_content),
                "remaining_slides": remaining_slides,
                "projected_end_ms": projected_end,
                "early_limit_ms": early_limit,
            }
        )
    tick.metrics["required_ratio"] = round(r, 4) if r is not None else None

    tick.metrics["schedule"] = _schedule(tick, r, projected_end, early_limit, limit)

    if t > limit:
        tick.detections.append(
            Detection(
                issue_type=Issue.TIME_OVER,
                severity=1.0,
                confidence=1.0,
                slide_number=tick.slide_number,
                evidence={"elapsed_ms": t, "limit_ms": limit, "over_ms": t - limit},
            )
        )

    # 계획대로 가는 발표자에게 '결론으로'는 틀린 말이다.
    # 계획상 마지막 1분에 3번 장이 정상일 수 있다.
    # 장별 계획이 없을 때(FE 코치와 같은 대체 판단)나 실제로 늦을 때만 띄운다
    behind_now = r is not None and r >= cfg.behind_ratio
    if (
        0 < remaining <= cfg.final_minute_ms
        and (remaining_slides is None or remaining_slides >= 2)
        and (r is None or behind_now)
    ):
        tick.detections.append(
            Detection(
                issue_type=Issue.FINAL_MINUTE,
                severity=0.8,
                confidence=1.0,
                slide_number=tick.slide_number,
                evidence={"remaining_ms": remaining, "remaining_slides": remaining_slides},
            )
        )

    if r is not None and r >= cfg.behind_ratio:
        _behind(tick, r, remaining, remaining_content, remaining_slides, confidence)

    # '빠름'은 비율 r 로 보지 않는다 — 끝으로 갈수록 분모가 작아져 몇 초 앞선 것만으로 r 이 크게
    # 흔들린다(9초 앞섬 → r 0.82). 대신 '지금 속도면 허용 최소 시간보다 일찍 끝나나'를 본다
    if (
        projected_end is not None
        and t >= cfg.ahead_min_elapsed_ratio * target
        and projected_end < early_limit
    ):
        tick.detections.append(
            Detection(
                issue_type=Issue.AHEAD_OF_SCHEDULE,
                severity=ramp(early_limit - projected_end, 0, cfg.early_end_bad_ratio * target),
                confidence=confidence,
                slide_number=tick.slide_number,
                metric="projected_end_ms",
                evidence={
                    "projected_end_ms": projected_end,
                    "early_limit_ms": early_limit,
                    "target_ms": target,
                },
            )
        )

    # 마지막 장에서는 '다음 장으로'가 말이 안 된다 — 그때는 FINAL_MINUTE / TIME_OVER 가 맡는다
    if (
        current is not None
        and current.target_ms > 0
        and slide_elapsed is not None
        and remaining_slides is not None
        and remaining_slides >= 2
        and slide_elapsed > current.target_ms * cfg.slide_over_factor
    ):
        over = slide_elapsed - current.target_ms
        tick.detections.append(
            Detection(
                issue_type=Issue.SLIDE_OVER,
                severity=ramp(
                    slide_elapsed / current.target_ms,
                    cfg.slide_over_factor,
                    cfg.slide_over_bad_factor,
                ),
                confidence=1.0,
                slide_number=tick.slide_number,
                metric="slide_elapsed_ms",
                params={"over_time_ms": over},
                evidence={
                    "slide_elapsed_ms": slide_elapsed,
                    "slide_target_ms": current.target_ms,
                    "over_ms": over,
                },
            )
        )


def _behind(
    tick: Tick,
    r: float,
    remaining: int,
    remaining_content: float | None,
    remaining_slides: int | None,
    confidence: float,
) -> None:
    cfg = tick.cfg.timing
    fast = tick.cfg.speech.fast_cpm
    cpm = tick.metrics.get("cpm")
    # 지금 속도에 r 을 곱한 값이 '빠름' 기준을 넘으면 속도로는 못 따라잡는다 → 내용을 줄인다
    if cpm is not None:
        required_cpm: float | None = round(cpm * r, 1)
        min_step = 1 if required_cpm > fast else 0
    else:
        required_cpm = None
        min_step = 1 if r > cfg.condense_ratio_without_pace else 0
    tick.detections.append(
        Detection(
            issue_type=Issue.BEHIND_SCHEDULE,
            severity=ramp(r, cfg.behind_ratio, cfg.behind_ratio_bad),
            confidence=confidence,
            slide_number=tick.slide_number,
            metric="required_ratio",
            min_step=min_step,
            params={"remaining_slides": remaining_slides, "remaining_time_ms": remaining},
            evidence={
                "required_ratio": round(r, 4),
                "remaining_content_ms": (
                    round(remaining_content) if remaining_content is not None else None
                ),
                "remaining_ms": remaining,
                "remaining_slides": remaining_slides,
                "cpm": cpm,
                "required_cpm": required_cpm,
            },
        )
    )


def _schedule(
    tick: Tick, r: float | None, projected_end: int | None, early_limit: int, limit: int
) -> Schedule:
    cfg = tick.cfg.timing
    target = tick.req.plan.target_ms or limit
    if tick.t > limit:
        return Schedule.OVER
    if r is None:
        return Schedule.UNKNOWN
    if r >= cfg.behind_ratio:
        return Schedule.BEHIND
    if (
        projected_end is not None
        and projected_end < early_limit
        and tick.t >= cfg.ahead_min_elapsed_ratio * target
    ):
        return Schedule.AHEAD
    return Schedule.ON_TRACK
