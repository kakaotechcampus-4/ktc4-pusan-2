"""서버 입구: 1초 기록이 들어온 그대로일 때 (끊김 · 유실 · 순서 뒤바뀜 · 이상한 기록).

* 기록이 오지 않은 시간은 '측정 못 함'이다. 오래된 마지막 상태로 피드백하지 않고,
  길어지면 GAZE_UNMEASURABLE(시선 피드백 보류 신호)이 된다;
* 요약의 coverage 는 테이크 전체 시간으로 나눈다;
* 뜻을 읽을 수 없는 기록은 그 1초만 버리고 나머지는 쓴다.
"""

from __future__ import annotations

import pytest

from gaze.config import EvidenceConfig
from gaze.core import (
    GazeSample,
    evaluate_gaze,
    intervention_outcome,
    normalize_samples,
    take_summary,
)
from gaze.schemas import parse_records

CFG = EvidenceConfig()


def seconds(*spec: tuple[str, int], t0: int = 0) -> list[GazeSample]:
    out, t = [], t0
    for state, n in spec:
        for _ in range(n):
            out.append(GazeSample(t, 1000, state, None, 1.0, 1.0, (), 8))
            t += 1000
    return out


def issue_types(timeline, t_ms):
    return [i["issue_type"] for i in evaluate_gaze(timeline, t_ms, CFG)]


# ==========================================================================
# 끊김
# ==========================================================================


def test_after_the_camera_drops_the_old_state_is_not_reported_and_gaze_becomes_unmeasurable():
    # 10초 청중, 4초 대본을 보다가 14초에 카메라가 끊겨 기록이 멈췄다
    records = seconds(("CAMERA", 10), ("BOTTOM", 4))
    assert issue_types(normalize_samples(records, start_ms=0, end_ms=14000), 14000) == [
        "GAZE_ON_SCRIPT"
    ]
    # 2초 뒤: 마지막 연속 구간이 '측정 못 함'이라 대본 이슈가 사라진다
    assert issue_types(normalize_samples(records, start_ms=0, end_ms=16000), 16000) == []
    # 더 지나면 시선 피드백 보류 신호
    for now in (30000, 120000):
        timeline = normalize_samples(records, start_ms=0, end_ms=now)
        (issue,) = evaluate_gaze(timeline, now, CFG)
        assert (issue["issue_type"], issue["actionable"]) == ("GAZE_UNMEASURABLE", False)


def test_the_take_summary_counts_the_lost_time():
    records = seconds(("CAMERA", 10), ("BOTTOM", 4))
    summary = take_summary(normalize_samples(records, start_ms=0, end_ms=120000), CFG)
    assert (summary["tracked_ms"], summary["measured_ms"]) == (120000, 14000)
    assert summary["coverage"] == pytest.approx(14000 / 120000, abs=1e-4)
    assert summary["state_ms"]["UNMEASURED"] == 106000


def spans(timeline):
    return [(s.t_ms, s.duration_ms, s.state) for s in timeline.samples]


def test_a_hidden_tab_leaves_a_gap_that_is_filled_as_unmeasured():
    records = seconds(("CAMERA", 10)) + seconds(("CAMERA", 10), t0=20000)
    timeline = normalize_samples(records)
    assert spans(timeline)[9:12] == [
        (9000, 1000, "CAMERA"),
        (10000, 10000, "UNMEASURED"),
        (20000, 1000, "CAMERA"),
    ]
    assert len(timeline.runs()) == 3  # CAMERA, UNMEASURED, CAMERA
    assert take_summary(timeline, CFG)["coverage"] == pytest.approx(20 / 30, abs=1e-4)


def test_a_take_without_records_is_unmeasured_throughout():
    timeline = normalize_samples([], start_ms=0, end_ms=8000)
    assert spans(timeline) == [(0, 8000, "UNMEASURED")]
    assert issue_types(timeline, 8000) == ["GAZE_UNMEASURABLE"]


def test_a_gap_keeps_its_exact_length():
    timeline = normalize_samples(seconds(("CAMERA", 2)), start_ms=0, end_ms=3500)
    assert spans(timeline)[2:] == [(2000, 1500, "UNMEASURED")]


def test_a_gap_reads_the_same_as_unmeasured_seconds():
    # 빈 시간을 기록 하나로 채워도, 기기가 1초마다 UNMEASURED 를 보낸 것과 읽는 결과가 같다
    records = seconds(("CAMERA", 8), ("BOTTOM", 4)) + seconds(("OTHER", 3), ("CAMERA", 5), t0=40000)
    filled = normalize_samples(records, start_ms=0, end_ms=60000)
    per_second = normalize_samples(
        seconds(("CAMERA", 8), ("BOTTOM", 4), ("UNMEASURED", 28), ("OTHER", 3), ("CAMERA", 5))
        + seconds(("UNMEASURED", 12), t0=48000)
    )
    per_second.samples = [
        GazeSample(s.t_ms, s.duration_ms, s.state) if s.state == "UNMEASURED" else s
        for s in per_second.samples
    ]
    assert take_summary(filled, CFG) == take_summary(per_second, CFG)
    for now in range(1000, 60001, 500):
        assert evaluate_gaze(filled, now, CFG) == evaluate_gaze(per_second, now, CFG)
    for issue_type in ("GAZE_ON_SCRIPT", "GAZE_AWAY", "GAZE_LOW_EYE_CONTACT"):
        for at in (12000, 40000, 43000):
            assert intervention_outcome(filled, at, issue_type, CFG) == intervention_outcome(
                per_second, at, issue_type, CFG
            )


# ==========================================================================
# 순서 · 중복 · 겹침
# ==========================================================================


def test_records_out_of_order_or_resent_read_the_same():
    records = seconds(("CAMERA", 3), ("BOTTOM", 3))
    shuffled = [records[4], records[0], records[5], records[2], records[1], records[3], records[2]]
    assert normalize_samples(shuffled).samples == records


def test_an_overlapping_record_is_dropped():
    records = seconds(("CAMERA", 3))
    overlapping = GazeSample(1500, 1000, "BOTTOM", None, 1.0, 1.0, (), 8)
    assert normalize_samples([*records, overlapping]).samples == records


def test_the_first_record_received_for_a_second_is_kept():
    first = GazeSample(0, 1000, "CAMERA", None, 1.0, 1.0, (), 8)
    resent = GazeSample(0, 1000, "BOTTOM", None, 1.0, 1.0, (), 8)
    assert normalize_samples([first, resent]).samples == [first]


def test_a_complete_take_is_unchanged():
    records = seconds(("CAMERA", 5), ("OTHER", 2), ("BOTTOM", 3))
    timeline = normalize_samples(records, start_ms=0, end_ms=10000)
    assert timeline.samples == records


# ==========================================================================
# 이상한 기록
# ==========================================================================


def test_a_record_outside_the_take_is_dropped():
    # 다른 단위의 시각(예: epoch ms)으로 온 기록, 테이크 시작 전에 끝난 기록
    records = seconds(("CAMERA", 10))
    stray = [
        GazeSample(1_760_000_000_000, 1000, "BOTTOM", None, 1.0, 1.0, (), 8),
        GazeSample(-5000, 1000, "BOTTOM", None, 1.0, 1.0, (), 8),
    ]
    timeline = normalize_samples([*records, *stray], start_ms=0, end_ms=20000)
    assert spans(timeline) == spans(normalize_samples(records)) + [(10000, 10000, "UNMEASURED")]


def test_a_record_across_the_take_edge_is_kept():
    edge = GazeSample(9500, 1000, "CAMERA", None, 1.0, 1.0, (), 8)
    assert normalize_samples([edge], start_ms=0, end_ms=10000).samples[-1] == edge


def test_a_wrong_clock_costs_one_record_not_the_server():
    # 지금 시각을 다른 단위로 줘도 빈 시간은 기록 하나다 (1초씩 채우면 수십억 개)
    timeline = normalize_samples(seconds(("CAMERA", 10)), start_ms=0, end_ms=1_760_000_000_000)
    assert len(timeline.samples) == 11
    assert issue_types(timeline, 1_760_000_000_000) == ["GAZE_UNMEASURABLE"]


def test_an_unreadable_record_costs_its_second_not_the_take():
    wire = [s.to_dict() for s in seconds(("CAMERA", 5))]
    wire[2] = {**wire[2], "state": "LOOKING_AROUND"}  # 모르는 상태
    wire[3] = {**wire[3], "confidence": 3.0}  # 범위 밖
    samples, dropped = parse_records(wire)
    assert dropped == 2
    timeline = normalize_samples(samples, start_ms=0, end_ms=5000)
    assert spans(timeline) == [
        (0, 1000, "CAMERA"),
        (1000, 1000, "CAMERA"),
        (2000, 2000, "UNMEASURED"),
        (4000, 1000, "CAMERA"),
    ]
