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
from coach_lab.simulator import Scenario, check_expect, run

SCENARIOS = sorted(SCENARIOS_DIR.glob("*.json"))


def test_there_are_scenarios():
    assert len(SCENARIOS) >= 10


@pytest.mark.parametrize("path", SCENARIOS, ids=lambda p: p.stem)
def test_scenario_meets_expectations(path: Path):
    result = run(Scenario.load(path))
    assert check_expect(result) == []


def test_same_input_same_decisions():
    sc = Scenario.load(SCENARIOS[2])
    a, b = run(sc), run(sc)
    assert a.events == b.events
    assert a.timeline == b.timeline


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


def test_review_evidence_numbers_are_finite_and_json():
    """리뷰 근거의 숫자는 모두 유한하고 JSON 으로 나간다 — 리뷰 에이전트가 그대로 인용한다."""
    path = next(p for p in SCENARIOS if p.stem == "14_recurring_persists")
    result = run(Scenario.load(path))
    blob = result.review.model_dump(mode="json")
    assert all(math.isfinite(v) for v in _walk(blob))
    json.dumps(blob)


def test_config_file_is_read_outside_the_core(tmp_path: Path):
    """설정 덮어쓰기 JSON 은 coach_lab 이 읽고, 코어는 같은 값을 키워드로 받는다."""
    path = tmp_path / "override.json"
    path.write_text(json.dumps({"policy": {"cooldown_ms": 45_000}}), encoding="utf-8")
    from_file = load_config_file(path)
    assert from_file.policy.cooldown_ms == 45_000
    assert from_file.config_hash() == load_config(policy={"cooldown_ms": 45_000}).config_hash()
