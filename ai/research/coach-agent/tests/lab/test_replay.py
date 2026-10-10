"""재생 — 시나리오 전체를 흘려보내 expect 를 확인한다. 같은 입력이면 같은 결과여야 한다."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import pytest

from coach.config import load_config
from coach_lab.paths import SCENARIOS_DIR
from coach_lab.replay import load_config_file
from coach_lab.simulator import Presenter, Scenario, check_expect, run

SCENARIOS = sorted(SCENARIOS_DIR.glob("*.json"))


def test_there_are_scenarios():
    assert len(SCENARIOS) >= 10


@pytest.mark.parametrize("path", SCENARIOS, ids=lambda p: p.stem)
def test_scenario_meets_expectations(path: Path):
    result = run(Scenario.load(path))
    assert check_expect(result) == []


def test_take_start_has_no_past_second():
    """Take 시작 순간(t=0)에는 지난 1초가 없다 — 시선 · 음량 기록을 보내지 않는다."""
    inputs = Presenter(Scenario.load(SCENARIOS[0])).request(0, None)["inputs"]
    assert inputs["gaze_records"] == []
    assert inputs["voice_records"] == []


def test_request_carries_raw_inputs_only():
    """코치에는 원자료만 간다: 1초 기록, 확정 단어(군더더기 표시 없이), 문장 끝 시각."""
    presenter = Presenter(Scenario.load(SCENARIOS[0]))
    for t in range(0, 41_000, 1_000):
        req = presenter.request(t, None)
    inputs = req["inputs"]
    assert "current" not in req
    assert len(inputs["gaze_records"]) == len(inputs["voice_records"]) == 30
    assert inputs["gaze_records"][-1]["t_ms"] == 39_000
    assert inputs["words"] and set(inputs["words"][0]) == {"word", "start_ms", "end_ms"}
    assert all(w["end_ms"] <= 40_000 for w in inputs["words"])
    assert all(40_000 - 60_000 <= u <= 40_000 for u in inputs["utterance_ends"])
    assert req["calibration"] == {"base_level_db": None}


def test_voice_record_has_the_voiced_time_of_that_second():
    """FE 처럼 지난 1초 동안 소리를 낸 시간을 잰다 — 1초 경계에 걸친 단어도 그 1초 몫만큼 든다.

    잡음 없는 발표에서는 정확하다(잡음이 있으면 경계 몫은 근사다. simulator._voiced_ms).
    """
    presenter = Presenter(Scenario.load(SCENARIOS[0]))
    for t in range(1_000, 61_000, 1_000):
        presenter.request(t, None)
    # 기대값은 Take 를 다 돌린 뒤의 단어로 따로 센다 (1초 끝에 말하던 단어는 다음 틱에 목록에 든다)
    heard = [w for w in presenter.words if w.mic]
    records = list(presenter.voice_records)[:-1]  # 마지막 1초 끝에 말하던 단어는 아직 없다
    partial = False
    for rec in records:
        lo, hi = rec["t_ms"], rec["t_ms"] + rec["duration_ms"]
        spoken = sum(max(0, min(w.end_ms, hi) - max(w.start_ms, lo)) for w in heard)
        assert rec["voiced_ms"] == min(spoken, hi - lo), rec
        assert (rec["level_db"] is None) == (rec["voiced_ms"] == 0)
        partial = partial or 0 < rec["voiced_ms"] < 1_000
    assert partial


def test_same_input_same_decisions():
    sc = Scenario.load(SCENARIOS[2])
    a, b = run(sc), run(sc)
    assert a.events == b.events
    assert a.timeline == b.timeline


@pytest.mark.parametrize("path", SCENARIOS, ids=lambda p: p.stem)
def test_event_ids_are_unique_within_a_take(path: Path):
    """Take 의 이벤트 id 는 finalize 가 낸 것까지 겹치지 않고, 같은 재생에서는 같다."""
    sc = Scenario.load(path)
    ids = [e["event_id"] for e in run(sc).events]
    assert len(set(ids)) == len(ids)
    assert ids == [e["event_id"] for e in run(sc).events]


def test_state_stays_small_over_a_long_take():
    """기록은 최근 60초만 남는다 — 10분 발표에서도 coach_state 가 커지지 않는다."""
    sc = Scenario.load(SCENARIOS[0]).model_copy(
        update={"duration_ms": 600_000, "end_on_finish": False, "plan": {"target_ms": 600_000}}
    )
    result = run(sc)
    assert result.final_state_bytes < 20_000


def test_decide_is_fast():
    result = run(Scenario.load(SCENARIOS[5]))
    # 실시간 경로 예산은 BE 타임아웃 300ms. 규칙만 쓰므로 수 ms 안이어야 한다
    assert result.stats()["decide_p95_ms"] < 20


def _walk(x: Any) -> list[float]:
    if isinstance(x, dict):
        return [v for item in x.values() for v in _walk(item)]
    if isinstance(x, list):
        return [v for item in x for v in _walk(item)]
    return [x] if isinstance(x, float) else []


def test_take_result_numbers_are_finite_and_json():
    """Take 결과의 숫자는 모두 유한하고 JSON 으로 나간다."""
    path = next(p for p in SCENARIOS if p.stem == "14_recurring_persists")
    result = run(Scenario.load(path))
    blob = result.take_result.model_dump(mode="json")
    assert all(math.isfinite(v) for v in _walk(blob))
    json.dumps(blob)


def _with_expect(name: str, expect: dict[str, Any]):
    """시나리오의 expect 만 바꿔 재생한다."""
    path = next(p for p in SCENARIOS if p.stem == name)
    sc = Scenario.load(path)
    return run(sc.model_copy(update={"expect": expect}))


def test_expect_segments_include_checks_every_given_field():
    ok = {"issue_type": "GAZE_ON_SCRIPT", "slide_number": 3, "coached": True, "reliable": True}
    assert check_expect(_with_expect("14_recurring_persists", {"segments_include": [ok]})) == []
    for wrong in ({"coached": False}, {"slide_number": 2}, {"issue_type": "PACE_FAST"}):
        failures = check_expect(
            _with_expect("14_recurring_persists", {"segments_include": [{**ok, **wrong}]})
        )
        assert len(failures) == 1 and "문제 구간" in failures[0]


def test_expect_no_segments_ignores_unreliable_segments():
    # 16 번은 GAZE 구간이 있지만 센서를 믿을 수 없어 reliable=false 다
    result = _with_expect("16_noisy_sensors", {"no_segments": [{"area": "GAZE"}]})
    assert any(
        s.area.value == "GAZE" and not s.reliable for s in result.take_result.problem_segments
    )
    assert check_expect(result) == []
    failures = check_expect(
        _with_expect(
            "14_recurring_persists", {"no_segments": [{"area": "GAZE", "slide_number": 3}]}
        )
    )
    assert len(failures) == 1 and "GAZE" in failures[0]
    other = {"no_segments": [{"area": "GAZE", "slide_number": 2}]}
    assert check_expect(_with_expect("14_recurring_persists", other)) == []


def test_expect_gave_up_include():
    ok = {"gave_up_include": [{"issue_type": "GAZE_ON_SCRIPT", "slide_number": 2}]}
    assert check_expect(_with_expect("03_gaze_gave_up", ok)) == []
    wrong = {"gave_up_include": [{"issue_type": "GAZE_ON_SCRIPT", "slide_number": 3}]}
    assert len(check_expect(_with_expect("03_gaze_gave_up", wrong))) == 1
    assert len(check_expect(_with_expect("02_gaze_effective", ok))) == 1


@pytest.mark.parametrize(
    ("operator", "value", "passes"),
    [
        ("LT", 0.9, True),
        ("LT", 0.8, False),
        ("LTE", 0.8, True),
        ("LTE", 0.79, False),
        ("GT", 0.7, True),
        ("GTE", 0.9, False),
    ],
)
def test_expect_slide_metrics_compare_the_slide_value(operator: str, value: float, passes: bool):
    # 14 번 3번 장의 대본 응시 비율은 0.8
    item = {
        "area": "GAZE",
        "slide_number": 3,
        "metric": "script_ratio",
        "operator": operator,
        "value": value,
    }
    failures = check_expect(_with_expect("14_recurring_persists", {"slide_metrics": [item]}))
    assert (failures == []) is passes


def test_expect_slide_metrics_fail_when_the_slide_was_not_measured():
    # 16 번 2번 장은 얼굴이 안 잡혀 시선 지표가 비어 있다
    item = {
        "area": "GAZE",
        "slide_number": 2,
        "metric": "script_ratio",
        "operator": "LTE",
        "value": 1.0,
    }
    assert len(check_expect(_with_expect("16_noisy_sensors", {"slide_metrics": [item]}))) == 1


def test_config_file_is_read_outside_the_core(tmp_path: Path):
    """설정 덮어쓰기 JSON 은 coach_lab 이 읽고, 코어는 같은 값을 키워드로 받는다."""
    path = tmp_path / "override.json"
    path.write_text(json.dumps({"policy": {"cooldown_ms": 45_000}}), encoding="utf-8")
    from_file = load_config_file(path)
    assert from_file.policy.cooldown_ms == 45_000
    assert from_file.config_hash() == load_config(policy={"cooldown_ms": 45_000}).config_hash()
