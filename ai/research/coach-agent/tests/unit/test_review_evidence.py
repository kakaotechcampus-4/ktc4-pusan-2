"""리뷰 근거 규칙 — 장별 표 · 구간 처리 · 미션 판정 · 기억 비교 · 순위 · 다음 미션 · 강점.

이벤트를 직접 만들어 build_review_evidence 에 넣는다. 실시간 판단과 떼어 규칙만 본다.
"""

from __future__ import annotations

from typing import Any

import pytest

from coach import build_review_evidence, finalize
from coach.config import load_config
from coach.review import mission_status, satisfies
from coach.schemas import EpisodeEvent, SlideEvent
from coach.vocab import MissionStatus

from .conftest import Session, gaze_script

_N = iter(range(10_000))


def slide_ev(n: int | None, start: int, end: int, **kw: Any) -> SlideEvent:
    total = end - start
    fields: dict[str, Any] = {
        "event_id": f"s{next(_N)}",
        "t_ms": end,
        "slide_number": n,
        "start_ms": start,
        "end_ms": end,
        "target_ms": None,
        "total_ms": total,
        "gaze_valid_ms": total,
        "gaze_script_ms": 0.1 * total,
        "gaze_unusable_ms": 0,
        "speech_ok_ms": total,
        "cpm_ms": total,
        "cpm_weighted": 300.0 * total,
        "filler_count": 0,
        "audio_live_ms": total,
        "speaking_ms": total,
        "db_ms": total,
        "db_weighted": 0.0,
        "long_silence_ms": 0,
        "chars_total": 0,
    }
    fields.update(kw)
    return SlideEvent(**fields)


def episode_ev(
    issue: str, ftype: str, slide: int | None, start: int, end: int, **kw: Any
) -> EpisodeEvent:
    fields: dict[str, Any] = {
        "event_id": f"e{next(_N)}",
        "t_ms": end + 3000,
        "candidate_id": f"{issue}-{start}",
        "issue_type": issue,
        "area": ftype,
        "slide_number": slide,
        "start_ms": start,
        "end_ms": end,
        "peak_severity": 0.8,
        "mean_severity": 0.8,
        "closed_by": "RESOLVED",
        "peak_evidence": {"window_ms": 10_000},
        "reliable_ms": end - start + 1000,
        "unreliable_ms": 0,
    }
    fields.update(kw)
    return EpisodeEvent(**fields)


PLAN = {
    "target_ms": 180_000,
    "min_ms": 165_000,
    "max_ms": 195_000,
    "slides": [
        {"slide_number": 1, "target_ms": 60_000},
        {"slide_number": 2, "target_ms": 60_000},
        {"slide_number": 3, "target_ms": 60_000},
    ],
}


def three_slides(**per_slide: dict[str, Any]) -> list[SlideEvent]:
    return [
        slide_ev(n, (n - 1) * 60_000, n * 60_000, target_ms=60_000, **per_slide.get(str(n), {}))
        for n in (1, 2, 3)
    ]


# ── 실시간 → SLIDE 이벤트 ─────────────────────────────────────────────────


def test_slide_events_summarise_each_slide():
    s = Session()
    for t in range(1_000, 20_001, 1_000):
        s.step(t, slide=1, slide_elapsed=t, gaze=gaze_script(0.9))
    for t in range(21_000, 40_001, 1_000):
        s.step(t, slide=2, slide_elapsed=t - 20_000, gaze=gaze_script(0.1))
    fin = finalize({"take_id": "test-take", "t_ms": 41_000, "coach_state": s.state})
    events = s.events + list(fin.events)
    slides = [e for e in events if e.kind == "SLIDE"]
    assert [e.slide_number for e in slides] == [1, 2]
    assert slides[0].end_ms == slides[1].start_ms == 21_000

    ev = build_review_evidence("test-take", events)
    by = {r.slide_number: r for r in ev.slides}
    assert by[1].script_ratio == pytest.approx(0.9, abs=0.01)
    assert by[2].script_ratio == pytest.approx(0.1, abs=0.01)
    assert by[1].gaze_coverage == 1.0


def test_take_without_slide_numbers_keeps_state_and_totals():
    s = Session(plan={"target_ms": 60_000}, slide=None)
    out = s.run(1_000, 30_000)
    assert all(
        "STATE_RESET" not in r.reason_codes for r in out
    )  # null 장 누적이 state 를 깨지 않는다
    fin = finalize({"take_id": "test-take", "t_ms": 31_000, "coach_state": s.state})
    slides = [e for e in fin.events if e.kind == "SLIDE"]
    assert len(slides) == 1 and slides[0].slide_number is None
    ev = build_review_evidence("test-take", s.events + list(fin.events), plan={"target_ms": 60_000})
    assert ev.slides == []
    assert ev.data_quality.duration_ms == 30_000


# ── 구간 처리 ─────────────────────────────────────────────────────────────


def test_unreliable_segment_is_not_a_problem():
    events = [
        *three_slides(),
        episode_ev(
            "GAZE_ON_SCRIPT", "GAZE", 2, 70_000, 90_000, reliable_ms=0, unreliable_ms=21_000
        ),
    ]
    ev = build_review_evidence("t", events, plan=PLAN)
    assert [s.hint.value for s in ev.segments] == ["UNRELIABLE"]
    assert not [i for i in ev.issues if i.area.value == "GAZE"]
    assert ev.data_quality.unreliable_segments == 1
    assert ev.summary.episodes == 0


def test_lag_compensation_moves_window_detections_back():
    events = [*three_slides(), episode_ev("GAZE_ON_SCRIPT", "GAZE", 2, 70_000, 90_000)]
    seg = build_review_evidence("t", events, plan=PLAN).segments[0]
    # 10초 창, 기준 0.7 → 시작은 7초, 끝은 3초 앞으로
    assert (seg.start_ms, seg.end_ms) == (70_000, 91_000)
    assert (seg.onset_ms, seg.offset_ms) == (63_000, 88_000)
    raw = build_review_evidence(
        "t", events, plan=PLAN, config=load_config(review={"lag_compensation": False})
    ).segments[0]
    assert (raw.onset_ms, raw.offset_ms) == (raw.start_ms, raw.end_ms)


def test_flicker_is_merged_into_one_segment():
    events = [
        *three_slides(),
        episode_ev("PACE_FAST", "SPEED", 2, 70_000, 80_000),
        episode_ev("PACE_FAST", "SPEED", 2, 85_000, 95_000),
    ]
    ev = build_review_evidence("t", events, plan=PLAN)
    assert len(ev.segments) == 1
    assert len(ev.segments[0].candidate_ids) == 2
    apart = build_review_evidence(
        "t",
        events,
        plan=PLAN,
        config=load_config(review={"merge_gap_ms": 0, "lag_compensation": False}),
    )
    assert len(apart.segments) == 2


def test_blips_are_dropped_unless_coached():
    blip = episode_ev("VOLUME_LOW", "VOLUME", 2, 70_000, 71_000)
    coached = episode_ev("VOLUME_LOW", "VOLUME", 3, 130_000, 131_000, intervention_ids=["ev-1"])
    ev = build_review_evidence("t", [*three_slides(), blip, coached], plan=PLAN)
    assert [s.slide_number for s in ev.segments] == [3]


# ── 미션 판정 ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("value", "op", "target", "want"),
    [
        (0.2, "LTE", 0.3, MissionStatus.ACHIEVED),
        (0.33, "LTE", 0.3, MissionStatus.PARTIAL),
        (0.5, "LTE", 0.3, MissionStatus.FAILED),
        (-4.0, "GTE", -6.0, MissionStatus.ACHIEVED),
        (-7.0, "GTE", -6.0, MissionStatus.FAILED),
        (0.3, "LT", 0.3, MissionStatus.PARTIAL),
    ],
)
def test_mission_status(value, op, target, want):
    tol = 0.05 if op in ("LTE", "LT") else 0.5
    assert mission_status(value, op, target, tol) == want


def test_satisfies_eq_and_strict():
    assert satisfies(1.0, "EQ", 1.0) and not satisfies(1.0, "LT", 1.0)


def test_missions_read_the_right_scope_and_say_why_not():
    events = three_slides(**{"2": {"gaze_script_ms": 0.6 * 60_000}})
    missions = [
        {
            "mission_id": "slide2",
            "area": "GAZE",
            "slide_number": 2,
            "target": {"metric": "script_ratio", "operator": "LTE", "value": 0.3},
        },
        {
            "mission_id": "take",
            "area": "SPEED",
            "target": {"metric": "cpm", "operator": "LTE", "value": 350},
        },
        {
            "mission_id": "time3",
            "area": "TIME",
            "slide_number": 3,
            "target": {"metric": "slide_duration_ms", "operator": "LTE", "value": 66_000},
        },
        {
            "mission_id": "never",
            "area": "GAZE",
            "slide_number": 9,
            "target": {"metric": "script_ratio", "operator": "LTE", "value": 0.3},
        },
        {
            "mission_id": "weird",
            "area": "GAZE",
            "target": {"metric": "eyebrow_height", "operator": "LTE", "value": 1},
        },
        {"mission_id": "vague", "area": "GAZE"},
    ]
    got = {
        m.mission_id: m
        for m in build_review_evidence("t", events, plan=PLAN, missions=missions).mission_results
    }
    assert got["slide2"].status == MissionStatus.FAILED and got["slide2"].observed == pytest.approx(
        0.6
    )
    assert got["take"].status == MissionStatus.ACHIEVED and got["take"].achieved
    assert got["time3"].status == MissionStatus.ACHIEVED
    assert (got["never"].status, got["never"].reason) == (
        MissionStatus.NOT_EVALUABLE,
        "NOT_REACHED",
    )
    assert got["weird"].reason == "UNSUPPORTED_METRIC"
    assert got["vague"].reason == "NO_TARGET"


def test_mission_on_unreliable_data_is_not_evaluable():
    events = three_slides(
        **{"2": {"gaze_valid_ms": 10_000, "gaze_unusable_ms": 50_000, "gaze_script_ms": 1_000.0}}
    )
    m = build_review_evidence(
        "t",
        events,
        plan=PLAN,
        missions=[
            {
                "mission_id": "m",
                "area": "GAZE",
                "slide_number": 2,
                "target": {"metric": "script_ratio", "operator": "LTE", "value": 0.3},
            }
        ],
    ).mission_results[0]
    assert (m.status, m.reason) == (MissionStatus.NOT_EVALUABLE, "LOW_DATA_COVERAGE")


# ── 기억 비교 · 영역 상태 · 강점 ──────────────────────────────────────────────


def test_resolved_recurring_issue_becomes_improved_and_a_strength():
    ev = build_review_evidence(
        "t",
        three_slides(),
        plan=PLAN,
        memory={"recurring_issues": [{"area": "GAZE", "slide_number": 2}]},
    )
    assert ev.memory_check[0].label.value == "RESOLVED"
    status = {t.area.value: t.status.value for t in ev.type_status}
    assert status["GAZE"] == "IMPROVED"
    kinds = {(s.kind.value, s.area.value) for s in ev.strengths}
    assert ("RESOLVED_RECURRING", "GAZE") in kinds and ("CLEAN", "SPEED") in kinds


def test_recurring_issue_is_ranked_higher():
    # 부담만 보면 속도(31초)가 시선(25초)보다 크다. 이전 Take 에도 있던 시선이면 순위가 뒤집힌다
    events = [
        *three_slides(),
        episode_ev("GAZE_ON_SCRIPT", "GAZE", 2, 70_000, 90_000),
        episode_ev("PACE_FAST", "SPEED", 3, 130_000, 160_000),
    ]
    plain = build_review_evidence("t", events, plan=PLAN)
    assert plain.issues[0].area.value == "SPEED"
    remembered = build_review_evidence(
        "t", events, plan=PLAN, memory={"recurring_issues": [{"area": "GAZE", "slide_number": 2}]}
    )
    assert remembered.issues[0].area.value == "GAZE"
    assert remembered.issues[0].memory.value == "RECURRING"
    assert remembered.memory_check[0].label.value == "RECURRING"


def test_no_data_means_not_evaluable_not_strength():
    events = three_slides(
        **{
            str(n): {"gaze_valid_ms": 0, "gaze_script_ms": 0.0, "gaze_unusable_ms": 60_000}
            for n in (1, 2, 3)
        }
    )
    ev = build_review_evidence("t", events, plan=PLAN)
    status = {t.area.value: t.status.value for t in ev.type_status}
    assert status["GAZE"] == "NOT_EVALUABLE"
    assert ("CLEAN", "GAZE") not in {(s.kind.value, s.area.value) for s in ev.strengths}


def test_volume_without_measured_speech_is_not_evaluable():
    # 오디오는 살아 있었지만 말한 시간 중 음량을 잰 것은 20% — 음량 레벨 입력에서 기준이
    # 잡히기 전이다. 이 표본으로 지난 음량 문제가 '해결됐다'거나 미션을 이뤘다고 하지 않는다
    events = three_slides(
        **{str(n): {"db_ms": 12_000, "db_weighted": -8.0 * 12_000} for n in (1, 2, 3)}
    )
    ev = build_review_evidence(
        "t",
        events,
        plan=PLAN,
        memory={"recurring_issues": [{"area": "VOLUME", "slide_number": None}]},
        missions=[
            {
                "mission_id": "m",
                "area": "VOLUME",
                "slide_number": None,
                "target": {"metric": "voice_diff_db", "operator": "GTE", "value": -10.0},
            }
        ],
    )
    status = {t.area.value: t.status.value for t in ev.type_status}
    assert status["VOLUME"] == "NOT_EVALUABLE"
    assert ev.memory_check[0].label.value == "UNKNOWN"
    m = ev.mission_results[0]
    assert (m.status, m.reason) == (MissionStatus.NOT_EVALUABLE, "LOW_DATA_COVERAGE")


# ── 순위 · 다음 미션 ──────────────────────────────────────────────────────


def test_type_total_decides_the_first_priority():
    # 시선은 장마다 나뉘어 하나하나는 속도보다 작지만, 합치면 더 크다
    events = [
        *three_slides(),
        episode_ev("GAZE_ON_SCRIPT", "GAZE", 2, 70_000, 85_000),
        episode_ev("GAZE_ON_SCRIPT", "GAZE", 3, 125_000, 140_000),
        episode_ev("PACE_FAST", "SPEED", 2, 90_000, 110_000),
    ]
    ev = build_review_evidence("t", events, plan=PLAN)
    assert [i.area.value for i in ev.issues][:2] == ["GAZE", "GAZE"]
    status = {t.area.value: t.status.value for t in ev.type_status}
    assert status["GAZE"] == status["SPEED"] == "PRIORITY"
    first = ev.next_missions[0]
    assert first.area.value == "GAZE" and first.target.metric == "script_ratio"
    assert "TOP_BURDEN" in first.reason_codes


def test_next_mission_targets_are_reachable_and_checkable():
    events = [
        *three_slides(**{"3": {"gaze_script_ms": 0.9 * 60_000}}),
        episode_ev("GAZE_ON_SCRIPT", "GAZE", 3, 125_000, 170_000),
    ]
    nm = build_review_evidence("t", events, plan=PLAN).next_missions[0]
    # 0.9 에서 한 번에 0.3 까지가 아니라 0.7 로 — 도달할 만큼만
    assert (nm.slide_number, nm.target.metric, nm.target.operator, nm.target.value) == (
        3,
        "script_ratio",
        "LTE",
        0.7,
    )
    assert nm.observed == pytest.approx(0.9)


def test_time_issue_comes_from_slide_durations():
    events = [
        slide_ev(1, 0, 60_000, target_ms=60_000),
        slide_ev(2, 60_000, 150_000, target_ms=60_000),
        slide_ev(3, 150_000, 200_000, target_ms=60_000),
    ]
    ev = build_review_evidence("t", events, plan=PLAN)
    time_issues = [i for i in ev.issues if i.area.value == "TIME"]
    assert {(i.slide_number, i.issue_types[0].value) for i in time_issues} == {
        (2, "SLIDE_OVER"),
        (None, "TIME_OVER"),
    }
    nm = next(m for m in ev.next_missions if m.area.value == "TIME")
    assert nm.target.metric in ("slide_duration_ms", "duration_ms")
