"""시간 판정 — 계획 대비 진행을 공통 1초 판정 결과로 낸다."""

from __future__ import annotations

import re
from typing import Any

import pytest

from coach.config import DEFAULT_CONFIG, TimingConfig, criteria_version
from coach.schemas import JudgmentResult
from coach.timing import criteria, judge, severity, summarize
from coach.vocab import Issue

CFG = DEFAULT_CONFIG.timing
HIGHER = "HIGHER_IS_WORSE"
LOWER = "LOWER_IS_WORSE"


def make(
    t: int,
    *,
    slide: int | None = 3,
    started: int = 0,
    since: int | None = None,
    slides: list[tuple[int, int, int]] | None = None,
    target: int | None = 300_000,
    min_ms: int | None = 270_000,
    max_ms: int | None = 330_000,
    chars: dict[str, int] | None = None,
    stt_ok: dict[str, int] | None = None,
    dwell: dict[str, int] | None = None,
    pace: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """기본: 300초 발표, 3장(90 · 90 · 120초). slides 는 (번호, 목표 ms, 대본 글자 수)."""
    rows = slides if slides is not None else [(1, 90_000, 450), (2, 90_000, 450), (3, 120_000, 600)]
    return {
        "t_ms": t,
        "since_ms": t - 1_000 if since is None else since,
        "plan": {
            "target_ms": target,
            "min_ms": min_ms,
            "max_ms": max_ms,
            "slides": [
                {"slide_number": n, "target_ms": ms, "script_chars": c} for n, ms, c in rows
            ],
        },
        "slide": {"number": slide, "started_ms": started} if slide is not None else None,
        "slide_chars": chars or {},
        "slide_stt_ok_ms": stt_ok or {},
        "slide_dwell_ms": dwell or {},
        "pace": pace,
    }


def behind(**kw: Any) -> dict[str, Any]:
    """240초, 3장 40초째, 글자 수로 25% 진행 → r 1.5 (늦음)."""
    base: dict[str, Any] = {
        "started": 200_000,
        "chars": {"3": 150},
        "stt_ok": {"3": 39_000},
        "dwell": {"3": 39_000},
    }
    return make(240_000, **{**base, **kw})


def one(inputs: dict[str, Any], config: TimingConfig = CFG) -> JudgmentResult:
    results = judge(inputs, inputs["t_ms"], config)
    assert len(results) == 1
    return results[0]


def kinds(result: JudgmentResult) -> list[str]:
    return [i.issue_type for i in result.issues]


# ── 장 목표 보정 ─────────────────────────────────────────────────────────


def test_slide_targets_are_scaled_to_total() -> None:
    # 장 목표 합 150초 → 전체 300초에 맞춰 2배. 1장은 180초가 된다
    r = one(make(10_000, slide=1, slides=[(1, 90_000, 0), (2, 60_000, 0)]))
    assert r.metrics["slide_expected_ms"] == 180_000


def test_no_scaling_when_sum_matches() -> None:
    assert one(make(10_000)).metrics["slide_expected_ms"] == 120_000


# ── 진행도: 글자 수 vs 시간 ──────────────────────────────────────────────


def test_progress_by_chars_when_stt_trusted() -> None:
    r = one(behind())
    assert r.metrics["required_ratio"] == 1.5
    assert r.issues[0].confidence == CFG.progress_confidence_chars


def test_progress_by_time_when_stt_gap() -> None:
    # 머문 39초 중 STT 를 믿은 시간은 30초 → 시간으로 잰다 (40 / 120 초)
    r = one(behind(chars={"3": 540}, stt_ok={"3": 30_000}))
    # 남은 내용 = 120000 × (1 − 1/3) = 80000 → r = 80000 / 60000
    assert r.metrics["required_ratio"] == pytest.approx(1.3333)
    assert r.issues[0].confidence == CFG.progress_confidence_time


def test_any_stt_gap_switches_to_time() -> None:
    assert one(behind(stt_ok={"3": 39_000})).metrics["required_ratio"] == 1.5
    assert one(behind(stt_ok={"3": 38_999})).metrics["required_ratio"] != 1.5


def test_stt_gap_in_earlier_slide_does_not_affect_current_slide() -> None:
    # 2장은 STT 가 끊겼던 장(믿은 시간 0), 지금 보는 3장은 글자 수로 잰다
    r = one(behind(stt_ok={"2": 0, "3": 39_000}, dwell={"2": 90_000, "3": 39_000}))
    assert r.metrics["required_ratio"] == 1.5


def test_script_chars_zero_falls_back_to_time() -> None:
    r = one(
        make(
            240_000, started=200_000, dwell={"3": 39_000}, slides=[(1, 180_000, 0), (3, 120_000, 0)]
        )
    )
    # 40초 머묾 → 진행 1/3, 남은 내용 80000, 60000 으로 나눔
    assert r.metrics["required_ratio"] == pytest.approx(1.3333)


def test_slide_target_zero_means_no_slide_over() -> None:
    r = one(make(100_000, slide=1, slides=[(1, 0, 0), (2, 150_000, 0), (3, 150_000, 0)]))
    assert r.metrics["slide_time_ratio"] is None
    assert Issue.SLIDE_OVER not in kinds(r)


# ── 목표 시간 없음 · 목표 지남 ───────────────────────────────────────────


def test_no_target_is_not_measurable() -> None:
    r = one(make(60_000, target=None, min_ms=None, max_ms=None))
    assert not r.measurable
    assert r.state == "UNKNOWN"
    assert r.issues == []
    assert all(v is None for v in r.metrics.values())
    assert [p.values["planned_ms"] for p in r.tally] == [0] * len(r.tally)


def test_after_target_only_time_over() -> None:
    r = one(make(340_000))
    assert r.state == "OVER"
    assert r.metrics["required_ratio"] is None
    assert r.metrics["projected_end_ms"] is None
    assert kinds(r) == [Issue.TIME_OVER]


def test_after_target_without_slide_plan() -> None:
    r = one(make(310_000, slide=None, slides=[], max_ms=None))
    assert r.state == "OVER"
    assert kinds(r) == [Issue.TIME_OVER]


# ── 이슈 5종 경계 ────────────────────────────────────────────────────────


def test_time_over_boundary_and_severity() -> None:
    assert Issue.TIME_OVER not in kinds(one(make(330_000)))
    issue = one(make(331_000)).issues[0]
    assert issue.issue_type == Issue.TIME_OVER
    assert issue.evidence["over_ms"] == 1_000
    assert issue.threshold == 1.0
    assert issue.bad == CFG.time_over_bad_ratio
    assert issue.severity == severity(round(331_000 / 330_000, 4), 1.0, 1.05, HIGHER)


def test_final_minute_without_slide_plan() -> None:
    r = one(make(240_000, slide=None, slides=[]))
    assert kinds(r) == [Issue.FINAL_MINUTE]
    assert r.issues[0].evidence == {"remaining_ms": 60_000, "remaining_slides": None}
    # 남은 시간이 기준값 → 심각도 0.5
    assert r.issues[0].severity == 0.5


def test_final_minute_boundary() -> None:
    assert Issue.FINAL_MINUTE not in kinds(one(make(239_000, slide=None, slides=[])))
    assert Issue.FINAL_MINUTE in kinds(one(make(240_000, slide=None, slides=[])))
    # 목표 시간이 지나도 허용 최대 전까지는 낸다
    assert Issue.FINAL_MINUTE in kinds(one(make(300_000, slide=None, slides=[])))
    assert kinds(one(make(310_000, slide=None, slides=[]))) == [Issue.FINAL_MINUTE]


def test_state_without_slide_plan_and_between_target_and_max() -> None:
    # 장별 계획이 없으면 진행을 모르니 늦다고 하지 않는다
    assert one(make(100_000, slide=None, slides=[])).state == "ON_TRACK"
    # 목표는 지났지만 허용 최대 안이면 늦은 것이다
    assert one(make(310_000)).state == "BEHIND"


def test_final_minute_needs_enough_slides() -> None:
    # 마지막 장(남은 장 1)이면 늦어도 안 낸다
    assert Issue.FINAL_MINUTE not in kinds(one(behind()))
    # 남은 장이 2 이상이고 늦으면 낸다
    late = make(240_000, slide=2, started=225_000, chars={"2": 0}, stt_ok={"2": 15_000})
    assert Issue.FINAL_MINUTE in kinds(one(late))


def test_final_minute_not_when_on_track() -> None:
    # 계획대로(r < 기준)면 마지막 1분에 2장이어도 '결론으로'는 틀린 말이다
    rows = [(1, 120_000, 600), (2, 120_000, 450), (3, 60_000, 300)]
    r = one(make(240_000, slide=2, started=120_000, slides=rows, chars={"2": 440}))
    assert r.metrics["required_ratio"] is not None
    assert r.metrics["required_ratio"] < CFG.behind_ratio
    assert Issue.FINAL_MINUTE not in kinds(r)


def test_behind_boundary() -> None:
    # 3장 120000 중 285 / 600 자 → 남은 63000 / 60000 = 1.05. 기준값과 같아도 낸다
    r = one(behind(chars={"3": 285}))
    assert r.metrics["required_ratio"] == 1.05
    assert kinds(r) == [Issue.BEHIND_SCHEDULE]
    assert r.issues[0].severity == 0.5
    # 한 글자 더 말하면 기준 미만
    assert Issue.BEHIND_SCHEDULE not in kinds(one(behind(chars={"3": 286})))


def test_behind_speed_limit_with_pace() -> None:
    fast = {"cpm": 318.5, "measurable": True, "fast_threshold": 350.0}
    ev = one(behind(pace=fast)).issues[0].evidence
    assert ev["required_cpm"] == 477.8
    assert ev["speed_limit_exceeded"] is True
    # 필요 속도가 '빠름' 기준 안이면 속도로 따라잡을 수 있다
    slow = {"cpm": 200.0, "measurable": True, "fast_threshold": 350.0}
    ev = one(behind(pace=slow)).issues[0].evidence
    assert ev["required_cpm"] == 300.0
    assert ev["speed_limit_exceeded"] is False
    # 반올림 전 값으로 비교한다: 233.34 × 1.5 = 350.01 > 350 (반올림하면 350.0)
    edge = {"cpm": 233.34, "measurable": True, "fast_threshold": 350.0}
    ev = one(behind(pace=edge)).issues[0].evidence
    assert ev["required_cpm"] == 350.0
    assert ev["speed_limit_exceeded"] is True


def test_behind_speed_limit_without_pace_uses_condense_ratio() -> None:
    # r 1.5 > 1.3 → 초과
    ev = one(behind()).issues[0].evidence
    assert ev["required_cpm"] is None
    assert ev["speed_limit_exceeded"] is True
    # r 1.2 ≤ 1.3 → 초과 아님 (진행 0.4 → 남은 72000 / 60000)
    ev = one(behind(chars={"3": 240})).issues[0].evidence
    assert ev["speed_limit_exceeded"] is False
    # 속도를 못 쟀으면 없는 것과 같다
    unmeasured = {"cpm": 318.5, "measurable": False, "fast_threshold": 350.0}
    assert one(behind(pace=unmeasured)).issues[0].evidence["required_cpm"] is None


def test_slide_over_boundary() -> None:
    # 1장 목표 90초 × 1.5 = 135초. 135초는 안 내고 그보다 길면 낸다
    at = make(135_000, slide=1, since=134_000, chars={"1": 400})
    assert Issue.SLIDE_OVER not in kinds(one(at))
    r = one(make(136_000, slide=1, since=135_000, chars={"1": 400}, dwell={"1": 135_000}))
    issue = next(i for i in r.issues if i.issue_type == Issue.SLIDE_OVER)
    assert issue.evidence["over_ms"] == 46_000
    assert issue.threshold == CFG.slide_over_factor


def test_slide_over_not_on_last_slide() -> None:
    assert Issue.SLIDE_OVER not in kinds(one(make(250_000, chars={"3": 100})))


def test_slide_over_not_after_target() -> None:
    # 목표 시간이 지난 뒤에는 마지막 장이 아니어도 '다음 장으로'를 내지 않는다
    r = one(make(310_000, slide=1, since=309_000, chars={"1": 400}, dwell={"1": 309_000}))
    assert r.metrics["slide_time_ratio"] > CFG.slide_over_factor
    assert Issue.SLIDE_OVER not in kinds(r)


def test_ahead_fires_only_after_min_elapsed_and_below_early_limit() -> None:
    # 120초에 1 · 2장을 끝내고 3장 절반 → 예상 종료 240초 < 270초
    fast = make(120_000, started=100_000, chars={"3": 300}, stt_ok={"3": 20_000})
    r = one(fast)
    assert r.state == "AHEAD"
    issue = next(i for i in r.issues if i.issue_type == Issue.AHEAD_OF_SCHEDULE)
    assert issue.threshold == 1.0
    assert issue.bad == CFG.ahead_bad_ratio
    assert issue.evidence["early_limit_ms"] == 270_000
    # 초반(경과 < 20%)에는 판단을 미룬다
    early = make(50_000, slide=1, chars={"1": 400})
    assert Issue.AHEAD_OF_SCHEDULE not in kinds(one(early))


def test_ahead_not_when_on_pace() -> None:
    # 150초에 1장 끝 · 2장 시작 → 예상 종료 300초 ≥ 270초
    r = one(make(150_000, slide=2, started=90_000, stt_ok={"2": 60_000}, chars={"2": 0}))
    assert Issue.AHEAD_OF_SCHEDULE not in kinds(r)


def test_issue_order() -> None:
    # 시간 초과 + 마지막 1분이 겹칠 수는 없고(남은 시간 ≤ 0), 늦음과 장 초과는 겹칠 수 있다
    r = one(
        make(
            200_000,
            slide=2,
            started=0,
            chars={"2": 0},
            stt_ok={"2": 199_000},
            dwell={"2": 199_000},
        )
    )
    assert kinds(r) == [Issue.BEHIND_SCHEDULE, Issue.SLIDE_OVER]


def test_issues_use_time_area() -> None:
    r = one(behind())
    assert all(i.area == r.area == "TIME" and i.actionable for i in r.issues)


# ── 집계 조각 ────────────────────────────────────────────────────────────


def test_tally_one_second() -> None:
    r = one(make(240_000))
    assert [(p.t_ms, p.values) for p in r.tally] == [
        (239_000, {"elapsed_ms": 1_000, "planned_ms": 1_000})
    ]
    assert r.counted_until_ms == 240_000


def test_tally_gap_is_split_into_seconds() -> None:
    r = one(make(240_000, since=236_000))
    assert [p.t_ms for p in r.tally] == [236_000, 237_000, 238_000, 239_000]
    assert sum(p.values["elapsed_ms"] for p in r.tally) == 4_000


def test_tally_splits_at_slide_start() -> None:
    r = one(make(240_000, since=237_500, started=238_200))
    assert [(p.t_ms, p.values["elapsed_ms"]) for p in r.tally] == [
        (237_500, 700),
        (238_200, 1_000),
        (239_200, 800),
    ]


def test_tally_empty_when_nothing_new() -> None:
    for since in (240_000, 250_000):
        r = one(make(240_000, since=since))
        assert r.tally == []
        assert r.counted_until_ms == since


def test_current_dwell_counts_only_since_cursor() -> None:
    # 장이 190초에 시작했지만 앞 몫(49초)은 이미 누적에 들어 있다 → 이번에 더할 몫은 1초
    r = one(make(240_000, started=190_000, dwell={"3": 49_000}))
    assert r.metrics["slide_elapsed_ms"] == 50_000


# ── summarize · criteria · severity ──────────────────────────────────────


def test_summarize() -> None:
    out = summarize({"TIME": {"elapsed_ms": 4_000, "planned_ms": 3_000}})
    assert out == {"TIME": {"duration_ms": 4_000, "measured_ratio": 0.75}}


def test_summarize_zero_elapsed_and_missing() -> None:
    zero = {"TIME": {"duration_ms": 0, "measured_ratio": None}}
    assert summarize({"TIME": {"elapsed_ms": 0, "planned_ms": 0}}) == zero
    assert summarize({}) == zero
    assert summarize({"TIME": {}}) == zero


def test_criteria_keys_and_directions() -> None:
    c = criteria()
    assert set(c) == {
        Issue.TIME_OVER,
        Issue.FINAL_MINUTE,
        Issue.BEHIND_SCHEDULE,
        Issue.SLIDE_OVER,
        Issue.AHEAD_OF_SCHEDULE,
    }
    higher = {Issue.TIME_OVER, Issue.BEHIND_SCHEDULE, Issue.SLIDE_OVER}
    for issue, crit in c.items():
        assert crit.direction == (HIGHER if issue in higher else LOWER)
    assert c[Issue.TIME_OVER].threshold == 1.0
    assert c[Issue.AHEAD_OF_SCHEDULE].threshold == 1.0
    assert c[Issue.FINAL_MINUTE].threshold == CFG.final_minute_ms


def test_criteria_version_format_and_changes_with_config() -> None:
    r = one(make(240_000))
    assert re.fullmatch(r"timing-1\.0\+[0-9a-f]{12}", r.criteria_version)
    changed = TimingConfig(behind_ratio=1.1)
    assert criteria_version("timing-1.0", changed) != r.criteria_version
    assert one(make(240_000), changed).criteria_version != r.criteria_version


@pytest.mark.parametrize(
    ("value", "expected"),
    [(1.05, 0.5), (1.275, 0.75), (1.5, 1.0), (9.0, 1.0), (0.5, 0.5)],
)
def test_severity_higher_is_worse(value: float, expected: float) -> None:
    assert severity(value, 1.05, 1.5, HIGHER) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [(60_000, 0.5), (30_000, 0.75), (0, 1.0), (-5, 1.0), (90_000, 0.5)],
)
def test_severity_lower_is_worse(value: float, expected: float) -> None:
    assert severity(value, 60_000, 0, LOWER) == expected


def test_severity_bad_equals_threshold() -> None:
    assert severity(0.0, 1.0, 1.0, HIGHER) == 1.0
    assert severity(5.0, 2.0, 2.0, LOWER) == 1.0
