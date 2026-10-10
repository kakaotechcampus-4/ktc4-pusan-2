"""음량 레벨 · 표시 없는 단어 — FE · BE 가 가공하지 않은 값을 보내도 코치가 기준을 잡고
군더더기를 센다."""

from __future__ import annotations

import pytest

from coach import decide
from coach.evaluators.speech import compute_cpm, is_filler
from coach.schemas import Word

from .conftest import Session, make_request

SPEAKING = {"silence_ms": 0, "audio_live": True}


def voice(level: float | None = None, **kw) -> dict:
    return {**SPEAKING, "level_db": level, **kw}


def _cands(resp):
    return {c.issue.value: c for c in resp.candidates}


# ── 음량 ─────────────────────────────────────────────────────────────────


def test_relative_db_is_used_as_is_when_sent():
    s = Session(plan={})
    for t in range(1000, 4001, 1000):
        resp = s.step(t, voice=voice(-40.0, relative_db=-8.0))
    assert "VOLUME_LOW" in _cands(resp)
    assert s.state.get("voice_baseline_db") is None


def test_level_is_compared_with_the_given_baseline():
    s = Session(plan={})
    for t in range(1000, 4001, 1000):
        resp = s.step(t, voice=voice(-32.0, baseline_db=-24.0))
    assert "VOLUME_LOW" in _cands(resp)
    assert s.state["history"][-1]["relative_db"] == -8.0


def test_without_a_baseline_the_first_speech_becomes_the_baseline():
    s = Session(plan={})
    # 첫 발화 15초 (말한 1초 15개) — 기준을 잡는 동안은 음량을 판단하지 않는다
    for t in range(1000, 15_001, 1000):
        resp = s.step(t, voice=voice(-24.0 + (t // 1000) % 3 - 1))
        assert resp.indicators.volume.value == "UNKNOWN"
    assert s.state["voice_baseline_db"] == pytest.approx(-24.0)
    assert "voice_baseline_samples" not in s.state or s.state["voice_baseline_samples"] == []
    # 평소보다 8 dB 작게 말하면 '작다'
    for t in range(16_000, 19_001, 1000):
        resp = s.step(t, voice=voice(-32.0))
    assert "VOLUME_LOW" in _cands(resp)
    assert resp.indicators.volume.value == "LOW"


def test_silent_seconds_do_not_count_toward_the_baseline():
    s = Session(plan={})
    for t in range(1000, 20_001, 1000):
        s.step(t, voice={"level_db": None, "silence_ms": 2000, "audio_live": True})
    assert s.state.get("voice_baseline_db") is None
    assert s.state.get("voice_baseline_samples", []) == []


# ── 군더더기 ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("text", "flag", "want"),
    [
        ("음", None, True),
        ("어어", None, True),
        ("음...", None, True),
        ("으음", None, True),
        ("그", None, False),  # 뜻이 있을 수 있는 말은 세지 않는다
        ("이제", None, False),
        ("음식", None, False),
        ("음", False, False),  # BE 가 표시했으면 그대로
        ("그", True, True),
    ],
)
def test_filler_is_decided_by_the_word_unless_be_marked_it(text, flag, want):
    assert is_filler(Word(w=text, start_ms=0, end_ms=300, filler=flag)) is want


def test_unmarked_fillers_are_counted_and_left_out_of_speed():
    words = [
        {"w": "가나다", "start_ms": 0, "end_ms": 600},
        {"w": "음", "start_ms": 700, "end_ms": 1000},
        {"w": "가나다", "start_ms": 1100, "end_ms": 1700},
    ]
    cpm, speak_ms, n = compute_cpm([Word.model_validate(w) for w in words], 2000, 15_000)
    assert (speak_ms, n) == (1200, 2)
    assert cpm == pytest.approx(300.0)

    resp = decide(make_request(2000, speech={"words": words}))
    assert resp.coach_state["history"][-1]["filler_new"] == 1
