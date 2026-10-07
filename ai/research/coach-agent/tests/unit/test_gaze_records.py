"""시선 1초 기록 입력 — FE 가 요약 대신 1초 판정을 보내면 코치가 창 비율 · 지금 라벨을 계산한다."""

from __future__ import annotations

from typing import Any

import pytest

from coach import decide
from coach.config import load_config
from coach.evaluators.gaze import window_summary
from coach.schemas import GazeInput

from .conftest import gaze_script, make_request

CFG = load_config().gaze


def records(t_ms: int, states: list[str], *, start_ms: int | None = None) -> dict[str, Any]:
    """t_ms 에서 끝나는 1초 기록들 (states 의 마지막이 가장 최근 1초)."""
    first = t_ms - len(states) * 1000 if start_ms is None else start_ms
    return {
        "window_ms": 10_000,
        "records": [
            {"t_ms": first + i * 1000, "duration_ms": 1000, "state": s}
            for i, s in enumerate(states)
        ],
    }


def summary(t_ms: int, gaze: dict[str, Any]) -> tuple[dict[str, float], int, str | None, int]:
    return window_summary(GazeInput.model_validate(gaze), t_ms, CFG)


def _cands(resp):
    return {c.issue.value: c for c in resp.candidates}


def test_records_become_window_ratios_and_current_run():
    ratios, span, current, run = summary(20_000, records(20_000, ["CAMERA"] * 3 + ["BOTTOM"] * 7))
    assert span == 10_000
    assert ratios == pytest.approx({"CAMERA": 0.3, "BOTTOM": 0.7})
    assert (current, run) == ("BOTTOM", 7000)


def test_gaps_and_unmeasured_count_as_not_measured():
    # 10초 창 중 기록이 6초뿐이고 그중 1초는 UNMEASURED — 측정 못 한 시간은 5초
    gaze = records(20_000, ["BOTTOM", "UNMEASURED", "BOTTOM", "BOTTOM", "CAMERA", "BOTTOM"])
    ratios, _, current, _ = summary(20_000, gaze)
    assert ratios == pytest.approx({"UNCERTAIN": 0.5, "BOTTOM": 0.4, "CAMERA": 0.1})
    assert current == "BOTTOM"


def test_screen_and_other_are_visible_but_not_script():
    # 6상태 기록: SCREEN · OTHER 는 대본이 아니지만 '보인 시간'에는 들어간다
    states = ["SCREEN", "OTHER", "CAMERA", "UNCERTAIN"] + ["BOTTOM"] * 6
    ratios, _, _, _ = summary(20_000, records(20_000, states))
    assert ratios == pytest.approx(
        {"SCREEN": 0.1, "OTHER": 0.1, "CAMERA": 0.1, "UNCERTAIN": 0.1, "BOTTOM": 0.6}
    )
    # 보인 시간 중 대본 6 / 9 = 0.67 — 비율로는 0.7 미만이지만 6초 연속이라 연속 응시로 잡힌다
    resp = decide(make_request(20_000, gaze=records(20_000, states)))
    assert _cands(resp)["GAZE_SCRIPT"].status.value == "WAITING"  # 지속 3초 미달
    assert resp.indicators.gaze.value == "SCRIPT"


def test_run_breaks_at_a_gap():
    gaze = records(20_000, ["BOTTOM"] * 3, start_ms=10_000)
    gaze["records"] += [
        {"t_ms": 15_000 + i * 1000, "duration_ms": 1000, "state": "BOTTOM"} for i in range(5)
    ]
    _, _, current, run = summary(20_000, gaze)
    assert (current, run) == ("BOTTOM", 5000)


def test_stale_records_mean_the_current_label_is_unknown():
    # 마지막 기록이 5초 전에 끝났다 — 카메라 · 전송이 끊긴 것이다
    _, _, current, run = summary(20_000, records(15_000, ["BOTTOM"] * 5))
    assert (current, run) == ("UNCERTAIN", 5000)


def test_overlapping_records_are_not_counted_twice():
    gaze = records(20_000, ["BOTTOM"] * 10)
    gaze["records"].append({"t_ms": 18_000, "duration_ms": 1000, "state": "BOTTOM"})
    ratios, _, _, _ = summary(20_000, gaze)
    assert sum(ratios.values()) == pytest.approx(1.0)
    assert ratios["BOTTOM"] == pytest.approx(1.0)


def test_same_answer_as_the_summary_input():
    states = ["CAMERA", "BOTTOM", "BOTTOM", "UNCERTAIN", "BOTTOM"] * 2
    from_records = decide(make_request(30_000, gaze=records(30_000, states)))
    from_summary = decide(
        make_request(
            30_000,
            gaze={
                "window_ms": 10_000,
                "ratios": {"CAMERA": 0.2, "BOTTOM": 0.6, "UNCERTAIN": 0.2},
                "current_label": "BOTTOM",
                "current_label_ms": 1000,
            },
        )
    )
    assert from_records.candidates == from_summary.candidates
    assert from_records.indicators == from_summary.indicators


def test_no_gaze_judgement_right_after_the_take_starts():
    # 3초 동안 모두 대본 — 표본이 너무 적어 지적하지 않고 상태 표시는 '모름'
    resp = decide(make_request(3000, gaze=records(3000, ["BOTTOM"] * 3)))
    assert "GAZE_SCRIPT" not in _cands(resp)
    assert resp.indicators.gaze.value == "UNKNOWN"


@pytest.mark.parametrize(
    "gaze",
    [
        records(20_000, ["UNCERTAIN"] * 10),
        records(20_000, []),
        {"window_ms": 10_000, "ratios": {"UNCERTAIN": 1.0}, "current_label": "UNCERTAIN"},
        {"window_ms": 10_000, "ratios": {"UNMEASURED": 1.0}},
    ],
    ids=["uncertain-records", "no-records", "uncertain-ratios", "unmeasured-ratios"],
)
def test_nothing_measured_shows_uncertain_and_stays_in_the_sensor_history(gaze):
    resp = decide(make_request(20_000, gaze=gaze))
    assert resp.feedback is None
    assert resp.indicators.gaze.value == "UNCERTAIN"
    assert resp.coach_state["history"][-1]["gaze_uncertain"] == 1.0


def test_unmeasured_ratio_counts_against_the_sensor():
    ratios = gaze_script(0.85, uncertain=0.0)["ratios"]
    ratios = {k: v * 0.4 for k, v in ratios.items()} | {"UNMEASURED": 0.6}
    resp = decide(make_request(20_000, gaze={"window_ms": 10_000, "ratios": ratios}))
    c = _cands(resp)["GAZE_SCRIPT"]
    assert c.status.value == "IGNORED" and "SENSOR_UNUSABLE" in c.reasons
