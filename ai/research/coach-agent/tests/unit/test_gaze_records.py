"""시선 1초 기록 입력 — FE 가 요약 대신 1초 판정을 보내면 코치가 창 비율 · 지금 라벨을 계산한다."""

from __future__ import annotations

from typing import Any

import pytest

from coach.config import load_config
from coach.evaluators.gaze import window_summary
from coach.schemas import GazeInput

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
    return {c.issue_type.value: c for c in resp.candidates}


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
