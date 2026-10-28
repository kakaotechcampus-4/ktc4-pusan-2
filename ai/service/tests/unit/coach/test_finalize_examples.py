"""finalize 예시 계약 — 실제 판정 모듈 넷으로 판정하면 예시 응답과 같다 (기능 문서 5-5 · 11절).

예시는 연구 폴더 `coach_lab.examples` 가 같은 판정 모듈로 만든다. 여기서는 판정 모듈 넷과
기준 음량 함수 둘을 묶어 예시 요청을 다시 판정해, 서비스가 BE 에 낼 응답이 예시와 같은지 본다.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from pitch_coach_ai.features.coach import finalize
from pitch_coach_ai.features.coach.judges import Judges
from pitch_coach_ai.features.coach.schemas import FinalizeRequest, FinalizeResponse
from pitch_coach_ai.features.filler import core as filler
from pitch_coach_ai.features.gaze import core as gaze
from pitch_coach_ai.features.pace import core as pace
from pitch_coach_ai.features.volume import core as volume

from .test_examples import EXAMPLES, assert_same

FINALIZE = EXAMPLES / "finalize"

#: 서비스의 실제 판정 모듈 넷. 기준 음량은 소리 크기 모듈로 잡는다
JUDGES = Judges(
    gaze=gaze,
    pace=pace,
    volume=volume,
    filler=filler,
    speaking_levels=volume.speaking_levels,
    baseline=volume.baseline,
)


def _load(name: str) -> Any:
    return json.loads((FINALIZE / name).read_text(encoding="utf-8"))


@pytest.mark.parametrize("suffix", ["", "_replay"])
def test_finalize_response_matches_example(suffix: str) -> None:
    request = _load(f"request{suffix}.json")
    FinalizeRequest.model_validate(request)
    expected = _load(f"response{suffix}.json")
    FinalizeResponse.model_validate(expected)
    actual = finalize(request, JUDGES)
    assert_same(actual.model_dump(mode="json"), expected)


def test_finalize_example_judges_the_last_window_with_stt_closed() -> None:
    request = _load("request.json")
    assert request["coach_state"]["last_t_ms"] < request["t_ms"]  # 마지막 창을 판정한다
    assert request["calibration"] == {"base_level_db": -29.5}  # evaluate 예시와 같은 Take 상수
    last_word_end = max(w["end_ms"] for w in request["inputs"]["words"])
    replay_words = _load("request_replay.json")["replay"]["words"]
    assert last_word_end == max(w["end_ms"] for w in replay_words)


def test_finalize_examples_show_normal_and_replay() -> None:
    normal, replayed = _load("response.json"), _load("response_replay.json")
    assert normal["take_result"]["replayed"] is False
    assert replayed["take_result"]["replayed"] is True
    assert _load("request_replay.json")["coach_state"] is None
    # 원자료로 다시 만들어도 Take 결과의 지표 · 문제 구간은 같다
    assert replayed["take_result"]["areas"] == normal["take_result"]["areas"]
    assert replayed["take_result"]["problem_segments"] == normal["take_result"]["problem_segments"]
    # replay 응답 이벤트는 효과 기록이 없는 실제 개입의 NOT_MEASURED 뿐이다
    assert {e["kind"] for e in replayed["events"]} <= {"OUTCOME"}


def test_finalize_example_measures_every_area_with_criteria() -> None:
    """판정 모듈 넷이 모두 실제 모듈이라 영역마다 측정 비율 · 판정 기준 · 기준 버전이 찬다.

    빈 자리를 자리표시로 채우면 이 값들이 비어 공통 계약(architecture.md 7절)과 어긋난다(11절).
    """
    response = _load("response.json")
    take_result = response["take_result"]
    for area in ("GAZE", "SPEED", "VOLUME", "PAUSE", "FILLER", "TIME"):
        assert take_result["areas"][area]["measured_ratio"] is not None, area
    for module in ("gaze", "pace", "volume", "filler", "timing"):
        assert response["meta"]["criteria_versions"][module], module
    assert set(take_result["criteria"]) >= {"GAZE", "SPEED", "VOLUME", "PAUSE", "FILLER", "TIME"}
