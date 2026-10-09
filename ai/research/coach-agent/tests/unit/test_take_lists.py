"""finalize 의 Take 결과 목록 — 문제 구간 · 개입 · 포기, 이벤트 중복 거르기."""

from __future__ import annotations

from typing import Any

from coach import finalize
from coach.config import load_config
from coach.state import CoachState, dump_state

from .conftest import Session, gaze_on
from .fakes import FakeJudge, fake_judges


class LagJudge(FakeJudge):
    """속도 모듈의 판정 기준에 판정 지연(시작 5초 · 끝 2초)을 단 가짜."""

    def criteria(self) -> dict[str, Any]:
        base = super().criteria()
        return {k: {**v, "onset_lag_ms": 5_000, "offset_lag_ms": 2_000} for k, v in base.items()}


def _judges():
    return fake_judges(pace=LagJudge("pace"))


def episode(
    issue: str = "GAZE_AWAY",
    start: int = 10_000,
    end: int = 20_000,
    *,
    slide: int | None = 1,
    area: str = "GAZE",
    reliable: int = 10_000,
    unreliable: int = 0,
    severity: float = 0.6,
    ivs: list[str] | None = None,
    event_id: str | None = None,
) -> dict[str, Any]:
    return {
        "kind": "EPISODE",
        "event_id": event_id or f"ep-{issue}-{slide}-{start}",
        "t_ms": end,
        "candidate_id": f"{issue}@{slide}",
        "issue_type": issue,
        "area": area,
        "slide_number": slide,
        "start_ms": start,
        "end_ms": end,
        "peak_severity": 1.0,
        "intervention_ids": ivs or [],
        "closed_by": "RESOLVED",
        "reliable_ms": reliable,
        "unreliable_ms": unreliable,
        "mean_severity": severity,
    }


def intervention(t_ms: int, issue: str = "GAZE_AWAY", area: str = "GAZE") -> dict[str, Any]:
    return {
        "kind": "INTERVENTION",
        "event_id": f"iv-{t_ms}",
        "t_ms": t_ms,
        "intervention_id": f"iv-{t_ms}",
        "candidate_id": f"{issue}@1",
        "issue_type": issue,
        "area": area,
        "instruction": "LOOK_AT_CAMERA",
        "variant": "v1",
        "message": "카메라를 보세요",
        "priority": 50,
        "confidence": 0.9,
        "reason_codes": [],
        "slide_number": 1,
        "evidence": {},
    }


def outcome(iv: str, result: str = "EFFECTIVE") -> dict[str, Any]:
    return {
        "kind": "OUTCOME",
        "event_id": f"oc-{iv}",
        "t_ms": 30_000,
        "intervention_id": iv,
        "candidate_id": "GAZE_AWAY@1",
        "issue_type": "GAZE_AWAY",
        "area": "GAZE",
        "instruction": "LOOK_AT_CAMERA",
        "slide_number": 1,
        "outcome": result,
        "metric": "script_ratio",
        "before": 0.2,
        "after": 0.8,
    }


def strategy(change: str, t_ms: int, issue: str = "GAZE_AWAY", slide: int | None = 1):
    return {
        "kind": "STRATEGY",
        "event_id": f"st-{issue}-{slide}-{t_ms}",
        "t_ms": t_ms,
        "issue_type": issue,
        "area": "GAZE",
        "slide_number": slide,
        "change": change,
        "from_instruction": "LOOK_AT_CAMERA",
        "from_variant": "v1",
        "failures": 2,
        "intervention_id": "iv-1",
    }


def result(events: list[dict[str, Any]], *, t_ms: int = 100_000, spans=None, config=None):
    """센 구간이 Take 를 덮은 coach_state 와 받은 이벤트로 finalize 한다."""
    state = CoachState(
        last_t_ms=t_ms,
        covered=[[0, t_ms]],
        slide_spans=spans if spans is not None else {"1": [0, t_ms]},
    )
    req = {"take_id": "t", "t_ms": t_ms, "coach_state": dump_state(state), "events": events}
    return finalize(req, _judges(), config).take_result


def segs(r) -> list[tuple[str, int | None, int, int]]:
    return [(s.issue_type.value, s.slide_number, s.start_ms, s.end_ms) for s in r.problem_segments]


# ── 이벤트 중복 ──────────────────────────────────────────────────────────


def test_duplicate_events_count_once():
    e = episode()
    r = result([e, dict(e), intervention(15_000), intervention(15_000)])
    assert len(r.problem_segments) == 1
    assert len(r.interventions) == 1


def test_same_id_keeps_the_first_copy():
    first = episode(severity=0.2, event_id="x")
    second = episode(severity=0.9, event_id="x")
    (s,) = result([first, second]).problem_segments
    assert s.mean_severity == 0.2


# ── 문제 구간: 신뢰도 · 합치기 ───────────────────────────────────────────


def test_reliability_threshold():
    ok = episode(start=0, end=10_000, reliable=5_000, unreliable=5_000)  # 믿을 수 있는 비율 0.5
    bad = episode(start=30_000, end=40_000, reliable=4_000, unreliable=6_000)
    r = result([ok, bad])
    assert [s.reliable for s in r.problem_segments] == [True, False]


def test_reliability_follows_the_configured_floor():
    e = episode(reliable=4_000, unreliable=6_000)
    strict = load_config(take_result={"min_reliability": 0.3})
    assert result([e], config=strict).problem_segments[0].reliable is True


def test_merge_same_issue_slide_and_reliability_within_gap():
    a = episode(start=10_000, end=20_000, severity=0.4, reliable=10_000)
    b = episode(start=30_000, end=40_000, severity=0.8, reliable=30_000)
    (s,) = result([a, b]).problem_segments
    # 끝은 마지막으로 본 1초까지(+1초): 앞 구간이 21초에 끝나 간격 9초라 합친다
    assert (s.start_ms, s.end_ms) == (10_000, 41_000)
    # 지켜본 시간(1만 · 3만)으로 가중한 평균
    assert s.mean_severity == 0.7


def test_no_merge_beyond_the_gap():
    a = episode(start=10_000, end=20_000)  # 마지막으로 본 1초까지 21초
    b = episode(start=31_001, end=40_000)  # 간격 10.001초
    assert len(result([a, b]).problem_segments) == 2


def test_no_merge_across_issue_slide_or_reliability():
    base = episode(start=10_000, end=20_000)
    other_issue = episode("GAZE_LOW_EYE_CONTACT", 22_000, 32_000)
    other_slide = episode(start=22_000, end=32_000, slide=2)
    unreliable = episode(start=22_000, end=32_000, reliable=1_000, unreliable=9_000)
    spans = {"1": [0, 100_000], "2": [0, 100_000]}
    r = result([base, other_issue, other_slide, unreliable], spans=spans)
    assert len(r.problem_segments) == 4


# ── 문제 구간: 보정 · 자르기 ─────────────────────────────────────────────


def test_lag_shift_comes_from_the_criteria():
    e = episode("PACE_FAST", 30_000, 50_000, area="SPEED")
    (s,) = result([e]).problem_segments
    assert (s.start_ms, s.end_ms) == (25_000, 49_000)  # 51초(마지막 1초 포함) − 끝 지연


def test_no_shift_when_the_criterion_is_missing():
    (s,) = result([episode("GAZE_AWAY", 30_000, 50_000)]).problem_segments
    assert (s.start_ms, s.end_ms) == (30_000, 51_000)


def test_clipped_to_the_take():
    e = episode("PACE_FAST", 2_000, 99_000, area="SPEED", slide=None)
    (s,) = result([e], t_ms=60_000).problem_segments
    assert (s.start_ms, s.end_ms) == (0, 60_000)


def test_clipped_to_the_slide_span():
    e = episode("PACE_FAST", 30_000, 70_000, area="SPEED")
    (s,) = result([e], spans={"1": [28_000, 60_000]}).problem_segments
    assert (s.start_ms, s.end_ms) == (28_000, 60_000)


def test_revisited_slide_uses_first_start_to_last_end():
    # 장 1 은 0~10초에 보고 70~90초에 다시 봤다: 스팬은 0~90초
    e = episode("GAZE_AWAY", 5_000, 80_000)
    (s,) = result([e], spans={"1": [0, 90_000]}).problem_segments
    assert (s.start_ms, s.end_ms) == (5_000, 81_000)


def test_take_wide_issue_clips_only_to_the_take():
    e = episode("PACE_FAST", 3_000, 20_000, area="SPEED", slide=None)
    r = result([e], spans={"1": [50_000, 60_000]})
    assert segs(r) == [("PACE_FAST", None, 0, 19_000)]


def test_dropped_when_nothing_is_left_after_clipping():
    e = episode("GAZE_AWAY", 10_000, 20_000)
    assert result([e], spans={"1": [40_000, 60_000]}).problem_segments == []


# ── 문제 구간: 짧은 잡음 · coached ───────────────────────────────────────


def test_short_uncoached_is_dropped_but_coached_is_kept():
    short = episode(start=10_000, end=11_000)  # 본 시간 2초(마지막 1초 포함)
    kept = episode(start=50_000, end=51_000, ivs=["iv-50500"])
    r = result([short, kept])
    assert segs(r) == [("GAZE_AWAY", 1, 50_000, 52_000)]
    assert r.problem_segments[0].coached is True


def test_short_is_measured_before_the_shift():
    # 본 시간 3초(마지막 1초 포함)면 보정으로 앞당겨져도 남는다 (길이는 보정 전으로 잰다)
    e = episode("PACE_FAST", 30_000, 32_000, area="SPEED")
    assert segs(result([e])) == [("PACE_FAST", 1, 25_000, 31_000)]


def test_merged_segment_is_coached_if_any_part_is_coached():
    a = episode(start=10_000, end=20_000)
    b = episode(start=25_000, end=35_000, ivs=["iv-1"])
    (s,) = result([a, b]).problem_segments
    assert s.coached is True


def test_uncoached_segment_is_not_coached():
    (s,) = result([episode()]).problem_segments
    assert s.coached is False


def test_record_only_issues_are_included_and_sorted():
    a = episode("GAZE_ON_SCREEN", 40_000, 60_000)
    b = episode("GAZE_AWAY", 10_000, 20_000)
    c = episode("GAZE_UNMEASURABLE", 10_000, 20_000)
    assert [s[0] for s in segs(result([a, b, c]))] == [
        "GAZE_AWAY",
        "GAZE_UNMEASURABLE",
        "GAZE_ON_SCREEN",
    ]


# ── 개입 · 포기 ──────────────────────────────────────────────────────────


def test_interventions_join_their_outcome():
    r = result(
        [intervention(40_000), outcome("iv-40000"), intervention(20_000, "GAZE_LOW_EYE_CONTACT")]
    )
    first, second = r.interventions
    assert (first.t_ms, first.outcome) == (20_000, None)
    assert (first.metric, first.before, first.after) == (None, None, None)
    assert second.intervention_id == "iv-40000"
    assert second.outcome.value == "EFFECTIVE"
    assert (second.metric, second.before, second.after) == ("script_ratio", 0.2, 0.8)
    assert second.message == "카메라를 보세요" and second.instruction.value == "LOOK_AT_CAMERA"


def test_gave_up_comes_from_strategy_events_once_in_time_order():
    r = result(
        [
            strategy("GAVE_UP", 50_000, "PACE_FAST", 2),
            strategy("ESCALATED", 20_000, "FILLER_FREQUENT"),
            strategy("GAVE_UP", 30_000),
            strategy("GAVE_UP", 40_000),
        ]
    )
    assert [(g.issue_type.value, g.slide_number) for g in r.gave_up] == [
        ("GAZE_AWAY", 1),
        ("PACE_FAST", 2),
    ]


def test_episode_closed_by_this_finalize_joins_the_received_events():
    # 열려 있던 문제 구간이 Take 끝에 닫히며 만든 EPISODE 는 받은 이벤트가 없어도 목록에 든다
    s = Session()
    s.run(1_000, 9_000, **gaze_on())
    req = {"take_id": "t", "t_ms": 10_000, "coach_state": s.state, "events": []}
    fin = finalize(req, s.judges, s.config)
    assert [e.kind for e in fin.events if e.kind == "EPISODE"] == ["EPISODE"]
    assert len(fin.take_result.problem_segments) == 1
    # BE 가 같은 이벤트를 다시 보내도 한 번만 센다
    again = {**req, "events": [e.model_dump(mode="json") for e in s.events]}
    assert len(finalize(again, s.judges, s.config).take_result.interventions) == len(
        [e for e in s.events if e.kind == "INTERVENTION"]
    )
