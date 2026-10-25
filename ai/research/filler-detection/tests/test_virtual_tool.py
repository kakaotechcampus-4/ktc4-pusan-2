"""가상 평가 데이터 도구(`tools/make_virtual_data.py`) — 표기 읽기 · 시각 분포 · seed."""

from __future__ import annotations

import importlib.util
import random
from pathlib import Path

import pytest

TOOL = Path(__file__).resolve().parents[1] / "tools" / "make_virtual_data.py"
_spec = importlib.util.spec_from_file_location("make_virtual_data", TOOL)
assert _spec and _spec.loader
tool = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tool)


def _build(text: str, seed: int = 0) -> dict:
    return tool.build_utterance("t1", text, random.Random(seed))


def test_star_marks_a_gold_filler_and_tier_comes_from_the_dictionary():
    utt = _build("음* 그 진짜* 엄* 서비스")
    got = [(w["word"], w["label_filler"], w["label_tier"]) for w in utt["words"]]
    assert got == [
        ("음", True, "T1"),
        ("그", False, "T2"),
        ("진짜", True, "T3"),
        ("엄", True, None),
        ("서비스", False, None),
    ]


def test_punctuation_stays_in_the_word_and_tier_ignores_it():
    [w] = _build("어어.*")["words"]
    assert w["word"] == "어어." and w["label_tier"] == "T1" and w["label_filler"] is True


@pytest.mark.parametrize(
    ("pause", "lo", "hi"), [("", 0, 120), (",", 150, 380), ("..", 300, 900), ("|", 1100, 1800)]
)
def test_gap_before_a_word_follows_the_pause_token(pause, lo, hi):
    for seed in range(20):
        a, b = _build(f"서비스 {pause} 발표", seed)["words"]
        gap = b["start_ms"] - a["end_ms"]
        assert lo - 1 <= gap <= hi + 1, (pause, seed, gap)


def test_word_length_follows_the_character_count_without_punctuation():
    for seed in range(20):
        [w] = _build("안녕하세요.", seed)["words"]  # 다섯 글자 + 마침표
        length = w["end_ms"] - w["start_ms"]
        assert 90 + 70 * 5 * 0.8 - 1 <= length <= 90 + 70 * 5 * 1.2 + 1


def test_sentence_end_signal_is_the_end_of_the_word_before_the_bar():
    utt = _build("가나 다라 | 마바 사아 |")
    words = utt["words"]
    assert utt["utterance_ends"] == [words[1]["end_ms"], words[3]["end_ms"]]
    assert words[2]["start_ms"] - words[1]["end_ms"] >= 1099
    assert utt["take_end_ms"] == words[-1]["end_ms"] + 1500


def test_first_word_starts_at_zero_and_there_is_no_signal_without_a_bar():
    utt = _build("가나 다라")
    assert utt["words"][0]["start_ms"] == 0
    assert utt["utterance_ends"] == []


@pytest.mark.parametrize("text", ["", ".. 가나", "가나 .. ..", "*"])
def test_bad_source_text_is_rejected(text):
    with pytest.raises(ValueError):
        _build(text)


def test_same_seed_gives_the_same_data_and_another_seed_does_not():
    source = [{"id": "a", "text": "음* 그 .. 진짜 | 어* 말"}, {"id": "b", "text": "그니까* 말"}]
    assert tool.build(source, 0) == tool.build(source, 0)
    assert tool.build(source, 0) != tool.build(source, 1)


def test_build_joins_utterances_into_one_take_with_a_signal_between_them():
    source = [{"id": "a", "text": "가나 다라"}, {"id": "b", "text": "마바 | 사아"}]
    [take] = tool.build(source, 0)["takes"]
    words = take["words"]
    assert [w["utt"] for w in words] == ["a", "a", "b", "b"]
    # 앞 발화의 마지막 단어 끝이 문장 끝 신호가 되고 그 뒤 1.1~1.8초 쉰다
    assert words[1]["end_ms"] in take["utterance_ends"]
    assert 1_099 <= words[2]["start_ms"] - words[1]["end_ms"] <= 1_801
    # `|` 가 있던 발화 안의 신호도 시간선에 맞춰 남는다
    assert words[2]["end_ms"] in take["utterance_ends"]
    assert words[3]["start_ms"] - words[2]["end_ms"] >= 1_099
    assert take["take_end_ms"] == words[-1]["end_ms"] + 1500
