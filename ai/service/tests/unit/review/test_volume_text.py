"""소리 크기 표기 — 무엇과 견준 값인지 쓴다 (#1181).

다음 미션은 다음 Take 의 기준으로 판정하므로 '기준', 그 Take 를 말하는 개선점은
그 Take 의 기준 음량 출처('점검 때' · '발표 처음')와 견준다.
"""

from __future__ import annotations

from typing import Any

import pytest

from pitch_coach_ai.features.review.next_missions import _describe
from pitch_coach_ai.features.review.text import volume_base

from .conftest import run, segment


@pytest.mark.parametrize(
    ("source", "word"),
    [("CALIBRATION", "점검 때"), ("TAKE", "발표 처음"), (None, "기준"), ("OTHER", "기준")],
)
def test_volume_base_follows_base_level_source(req, source, word):
    req["take_result"]["areas"]["VOLUME"]["take"]["base_level_source"] = source
    assert volume_base(req["take_result"]) == word


def test_volume_base_without_volume_take_is_unknown(req):
    req["take_result"]["areas"].pop("VOLUME")
    assert volume_base(req["take_result"]) == "기준"
    assert volume_base({"areas": {"VOLUME": {"take": None}}}) == "기준"


@pytest.mark.parametrize(
    ("operator", "value", "text"),
    [
        ("GTE", -7.3, "기준보다 7.3dB 넘게 작아지지 않기"),
        ("GTE", 0, "기준만큼 크게 말하기"),
        ("GTE", 3, "기준보다 3dB 이상 크게 말하기"),
        ("GT", -1, "기준보다 1dB 이상 작아지지 않기"),
        ("GT", 0.0, "기준보다 크게 말하기"),
        ("GT", 1.5, "기준보다 1.5dB 넘게 크게 말하기"),
        ("LTE", -2, "기준보다 2dB 이상 작게 말하기"),
        ("LTE", 0, "기준보다 크지 않게 말하기"),
        ("LTE", 2, "기준보다 2dB 넘게 커지지 않기"),
        ("LT", -2, "기준보다 2dB 넘게 작게 말하기"),
        ("LT", 0, "기준보다 작게 말하기"),
        ("LT", 2, "기준보다 2dB 이상 커지지 않기"),
    ],
)
def test_volume_mission_says_base_and_size(operator, value, text):
    assert _describe("voice_diff_db", operator, value, None) == text
    assert _describe("voice_diff_db", operator, value, 2) == f"2번 장에서 {text}"


def volume_priority(req: dict[str, Any], source: str | None) -> dict[str, Any]:
    """목소리 크기만 PRIORITY 인 요청 (Take 전체에 작은 소리, voice_diff_db -2.1)"""
    req["previous_recurring_issues"] = []
    req["missions"] = []
    req["user_criteria"]["targets"] = []
    req["take_result"]["problem_segments"] = [segment("VOLUME_LOW", "VOLUME", None, 0, 60_000, 0.8)]
    req["take_result"]["areas"]["VOLUME"]["take"]["base_level_source"] = source
    return req


@pytest.mark.parametrize(
    ("source", "word"), [("CALIBRATION", "점검 때"), ("TAKE", "발표 처음"), (None, "기준")]
)
def test_volume_improvement_says_the_take_base(req, source, word):
    resp, _ = run(volume_priority(req, source))
    assert resp.dimensions["vocal_delivery"] == "PRIORITY"
    (improvement,) = [i for i in resp.improvements if i.area == "VOLUME"]
    assert improvement.text == f"{word}보다 조금 더 크게 말해 보세요"


@pytest.mark.parametrize("source", ["CALIBRATION", "TAKE", None])
def test_volume_mission_says_base_whatever_the_take_base(req, source):
    resp, _ = run(volume_priority(req, source))
    (mission,) = [m for m in resp.next_missions if m.target.metric == "voice_diff_db"]
    # -2.1 에서 한 걸음(1dB) 나아진 목표
    assert mission.target.model_dump() == {
        "metric": "voice_diff_db",
        "operator": "GTE",
        "value": -1.1,
    }
    assert mission.description == "기준보다 1.1dB 넘게 작아지지 않기"
