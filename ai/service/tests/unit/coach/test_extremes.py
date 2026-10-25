"""극단 입력과 규모 (위조한 `coach_state`, 한 시간 Take 복구).

- 위조한 `coach_state`: 모양은 맞지만 값을 바꾼 상태가 와도 코치는 예외 없이 계약대로
  응답한다. 무엇을 믿을지(정합성 검사 · 서명)는 아직 정하지 않았으므로(코치 기능 문서 15절)
  여기서는 깨지지 않는 것만 본다
- 한 시간 Take 복구(`replay`): 실제 시선 · 군더더기 모듈과 가짜 말 속도 · 소리 크기로
  finalize 를 한 번 부르고 걸린 시간과 Take 결과 크기를 잰다. 오래 걸려 기본 실행에서 빼고
  `pytest -m slow` 로 돈다
"""

import json
import time
from pathlib import Path

import pytest

from pitch_coach_ai.features import coach
from pitch_coach_ai.features.coach import decide_safe, finalize
from pitch_coach_ai.features.coach.schemas import CoachResponse
from pitch_coach_ai.features.filler import core as filler_core
from pitch_coach_ai.features.gaze import core as gaze_core

from .conftest import PLAN
from .fakes import fake_judges
from .test_replay_recovery import raw

EXAMPLES = Path(coach.__file__).resolve().parent / "examples"


def _evaluate_request() -> dict:
    return json.loads((EXAMPLES / "evaluate" / "request.json").read_text(encoding="utf-8"))


def _state() -> dict:
    return decide_safe(_evaluate_request(), fake_judges()).coach_state


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("last_t_ms", 3_600_000),  # 미래 시각
        ("last_t_ms", -1),
        ("cursors", {"gaze": {"since_ms": 10**12}}),
        ("cursors", {"gaze": {"since_ms": -5}}),
        ("interventions", -3),
        ("slide_number", 10**6),
        ("totals", {"GAZE": {"total_ms": -1.0}}),
    ],
    ids=[
        "future_last_t",
        "negative_last_t",
        "huge_cursor",
        "negative_cursor",
        "negative_count",
        "huge_slide",
        "negative_total",
    ],
)
def test_forged_state_still_gets_a_contract_response(field, value) -> None:
    req = _evaluate_request()
    state = _state()
    state[field] = value
    req["t_ms"] += 1_000
    req["coach_state"] = state
    res = decide_safe(req, fake_judges())
    assert CoachResponse.model_validate(res.model_dump(mode="json")) == res


@pytest.mark.slow
def test_one_hour_take_replay_finishes_inside_the_be_limit() -> None:
    t_end = 3_600_000
    replay = raw(t_end)
    replay["gaze_records"] = [
        {
            "t_ms": t,
            "duration_ms": 1_000,
            "state": "BOTTOM" if (t // 1_000) % 7 == 0 else "AUDIENCE",
            "direction": None,
            "confidence": 0.9,
            "reliability": 0.9,
            "issues": [],
            "frames": 12,
        }
        for t in range(0, t_end, 1_000)
    ]
    replay["slides"] = [{"number": i + 1, "started_ms": i * 120_000} for i in range(30)]
    replay["utterance_ends"] = list(range(5_000, t_end, 7_000))
    req = {
        "take_id": "hour",
        "t_ms": t_end,
        "plan": PLAN,
        "calibration": {"base_level_db": -30.0},
        "coach_state": None,
        "events": [],
        "replay": replay,
    }
    judges = fake_judges(gaze=gaze_core, filler=filler_core)
    started = time.perf_counter()
    res = finalize(req, judges)
    seconds = time.perf_counter() - started
    # 저장하는 payload(`take_result`)를 공백 없는 JSON 으로 잰다 (코치 기능 문서 12-1)
    payload = res.take_result.model_dump(mode="json")
    size = len(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    print(f"one-hour replay: {seconds:.1f}s, take_result {size / 1024:.0f} KB")
    # BE 의 replay finalize 시간 제한은 60초다(코치 기능 문서 8절)
    assert seconds < 60
    assert res.take_result.replayed is True
    assert res.take_result.duration_ms == t_end
    assert len(res.take_result.areas["TIME"].slides) == 30
