"""연구용 판정 대역 gaze — 계약 모양과 커서."""

from __future__ import annotations

from typing import Any

import pytest

from coach.vocab import Issue
from coach_lab.judges import gaze
from tests.lab.judge_helpers import check_shape, merged, total


def gaze_records(states: list[str], *, start: int = 0) -> list[dict[str, Any]]:
    return [
        {"t_ms": start + i * 1000, "duration_ms": 1000, "state": s} for i, s in enumerate(states)
    ]


def voiced(seconds: list[int], *, start: int = 0, ms: int = 800) -> list[dict[str, Any]]:
    return [{"t_ms": start + i * 1000, "voiced_ms": ms if i in seconds else 0} for i in range(40)]


def test_gaze_result_shape_and_determinism():
    inp = {"records": gaze_records(["CAMERA"] * 4 + ["BOTTOM"] * 8), "voiced": voiced(range(12))}
    a = gaze.judge(inp, 12_000)
    b = gaze.judge(inp, 12_000)
    assert a == b and len(a) == 1
    check_shape(a[0])
    assert a[0].evaluator == "gaze" and a[0].area == "GAZE"
    assert a[0].issues[0].issue_type == Issue.GAZE_ON_SCRIPT


def test_gaze_unmeasurable_cases():
    t0 = gaze.judge({"records": []}, 0)[0]
    assert not t0.measurable and t0.state == "UNMEASURABLE" and not t0.issues
    all_uncertain = gaze.judge({"records": gaze_records(["UNCERTAIN"] * 10)}, 10_000)[0]
    assert not all_uncertain.measurable and all_uncertain.state == "UNMEASURABLE"
    assert all_uncertain.metrics["script_ratio"] is None
    no_records = gaze.judge({"records": []}, 10_000)[0]
    assert not no_records.measurable


def test_gaze_no_issue_right_after_take_start():
    r = gaze.judge({"records": gaze_records(["BOTTOM"] * 4)}, 4_000)[0]
    assert r.measurable and r.state == "SCRIPT" and not r.issues
    r = gaze.judge({"records": gaze_records(["BOTTOM"] * 5)}, 5_000)[0]
    assert r.issues


def test_gaze_issue_when_sensor_unusable_is_not_actionable():
    # 대본 4초 + 얼굴 없음 6초 → 옛 평가기도 낸다 (코치가 센서 불량으로 남긴다)
    states = ["UNCERTAIN"] * 6 + ["BOTTOM"] * 4
    r = gaze.judge({"records": gaze_records(states)}, 10_000)[0]
    assert r.metrics["script_ratio"] is None and not r.measurable
    assert r.issues and not r.issues[0].actionable


def test_gaze_tally_by_state_and_cursor():
    states = ["CAMERA", "SCREEN", "BOTTOM", "OTHER", "UNCERTAIN", "UNMEASURED"]
    recs = gaze_records(states)
    r = gaze.judge({"records": recs, "voiced": voiced([2, 3])}, 6_000)[0]
    check_shape(r)
    t = merged([r])
    assert t == {
        "camera_ms": 1000,
        "screen_ms": 1000,
        "script_ms": 1000,
        "away_ms": 1000,
        "uncertain_ms": 1000,
        "unmeasured_ms": 1000,
        "measured_ms": 4000,
        "total_ms": 6000,
        "speaking_ms": 2000,
        "reading_ms": 1000,
    }
    # 이어서 부르면 새 시간만 센다
    recs = gaze_records(states + ["CAMERA"] * 3)
    first = gaze.judge({"records": recs, "voiced": voiced([2, 3])}, 6_000)[0]
    second = gaze.judge(
        {"records": recs, "voiced": voiced([2, 3]), "since_ms": first.counted_until_ms}, 9_000
    )[0]
    assert all(item.t_ms >= 6_000 for item in second.tally)
    assert total(second.tally, "total_ms") == 3000
    assert second.counted_until_ms == 9_000
    check_shape(second, since=6_000)
    again = gaze.judge({"records": recs, "since_ms": 9_000}, 9_000)[0]
    assert again.tally == [] and again.counted_until_ms == 9_000


def test_gaze_time_without_records_is_unmeasured():
    recs = gaze_records(["CAMERA"] * 2) + gaze_records(["CAMERA"] * 2, start=5000)
    r = gaze.judge({"records": recs, "since_ms": 0}, 7_000)[0]
    assert merged([r])["unmeasured_ms"] == 3000
    assert merged([r])["total_ms"] == 7000
    gap = [i for i in r.tally if "unmeasured_ms" in i.values]
    assert [i.t_ms for i in gap] == [2000]


def test_gaze_speaking_time_counts_without_a_gaze_record():
    # 말한 3초 중 1초만 대본을 봤고, 시선 기록은 그 1초에만 있다
    recs = gaze_records(["BOTTOM"], start=1000)
    v = [{"t_ms": s, "voiced_ms": 800} for s in (0, 1000, 2000)]
    r = gaze.judge({"records": recs, "voiced": v}, 3_000)[0]
    t = merged([r])
    assert t["speaking_ms"] == 3000 and t["reading_ms"] == 1000
    assert t["unmeasured_ms"] == 2000 and t["total_ms"] == 3000
    s = gaze.summarize({"GAZE": t})["GAZE"]
    assert s["reading_ratio"] == pytest.approx(1 / 3, abs=1e-4)
    # 말하지 않은 1초(300ms 미만)는 세지 않는다
    quiet = gaze.judge({"records": recs, "voiced": [{"t_ms": 0, "voiced_ms": 100}]}, 3_000)[0]
    assert "speaking_ms" not in merged([quiet])


def test_gaze_mean_reliability_uses_the_first_record_of_a_second():
    recs = gaze_records(["CAMERA"] * 4)
    recs[1]["reliability"] = 0.8
    dup = {"t_ms": 1000, "duration_ms": 1000, "state": "CAMERA", "reliability": 0.2}
    r = gaze.judge({"records": [*recs, dup]}, 4_000)[0]
    assert r.metrics["mean_reliability"] == pytest.approx((1 + 0.8 + 1 + 1) / 4)
    first = {**dup, "reliability": 0.2}
    r = gaze.judge({"records": [first, *recs]}, 4_000)[0]
    assert r.metrics["mean_reliability"] == pytest.approx((1 + 0.2 + 1 + 1) / 4)


def reading_runs(recs, voiced_s, since, t):
    r = gaze.judge({"records": recs, "voiced": voiced(voiced_s), "since_ms": since}, t)[0]
    return merged([r]).get("reading_runs", 0)


def test_gaze_reading_run_counts_when_it_ends():
    states = ["CAMERA", "CAMERA"] + ["BOTTOM"] * 4 + ["CAMERA"] * 4  # 대본 2~6초
    recs = gaze_records(states)
    speak = list(range(10))
    assert reading_runs(recs, speak, 0, 6_000) == 0  # 아직 이어지는 중
    assert reading_runs(recs, speak, 6_000, 7_000) == 1  # 끝난 1초
    assert reading_runs(recs, speak, 7_000, 9_000) == 0  # 이미 센 것
    assert reading_runs(recs, speak, 0, 10_000) == 1


def test_gaze_reading_run_rules():
    recs = gaze_records(["BOTTOM"] * 8 + ["CAMERA"] * 2)
    # 말하지 않은 1초가 끼면 끊긴다 → 3초 · 4초 두 구간 (3초 이상이라 둘 다 센다)
    assert reading_runs(recs, [0, 1, 2, 4, 5, 6, 7], 0, 10_000) == 2
    # 2초짜리는 세지 않는다
    assert reading_runs(recs, [0, 1], 0, 10_000) == 0
    # 기록이 빈 1초가 끼면 끊긴다
    gap = gaze_records(["BOTTOM"] * 2) + gaze_records(["BOTTOM"] * 2, start=3000)
    assert reading_runs(gap, range(10), 0, 10_000) == 0
    # UNCERTAIN 이 끼면 끊긴다
    cut = gaze_records(["BOTTOM", "BOTTOM", "UNCERTAIN", "BOTTOM", "BOTTOM", "CAMERA"])
    assert reading_runs(cut, range(10), 0, 6_000) == 0


def test_gaze_summarize_and_criteria():
    tally = {
        "GAZE": {
            "camera_ms": 52000,
            "script_ms": 28000,
            "screen_ms": 6000,
            "away_ms": 4000,
            "measured_ms": 90000,
            "total_ms": 96000,
            "speaking_ms": 70000,
            "reading_ms": 15000,
            "reading_runs": 4,
        }
    }
    s = gaze.summarize(tally)["GAZE"]
    assert s["audience_ratio"] == pytest.approx(0.5778, abs=1e-4)
    assert s["reading_ratio"] == pytest.approx(0.2143, abs=1e-4)
    assert s["reading_runs"] == 4 and s["measured_ratio"] == pytest.approx(0.9375)
    assert gaze.summarize({})["GAZE"]["audience_ratio"] is None
    c = gaze.criteria()["GAZE_ON_SCRIPT"]
    assert (c.metric, c.threshold, c.bad) == ("script_run_ms", 5000, 15000)
    assert (c.onset_lag_ms, c.offset_lag_ms) == (7000, 3000)
    assert c.direction == "HIGHER_IS_WORSE"
