"""보류 — 뒤 간격을 아직 알 수 없는 마지막 T2 · T3 단어 (filler.md 6-1 4, 6-2)."""

from __future__ import annotations

import pytest

from tests.helpers import PLAIN, run, verdicts, word

PENDING = "뒤 간격을 아직 알 수 없음"


def _last(text: str, **overrides):
    """ "매출은" 뒤에 `text` 가 마지막 단어로 오는 창 (filler.md 5-5 경계 예시)."""
    words = [word("매출은", 50300, 50800), word(text, 51500, 51650)]
    return run(52000, words, **overrides)


@pytest.mark.parametrize("text", ["그", "그니까"])
def test_last_t2_or_t3_word_is_pending_even_with_a_big_before_gap(text):
    last = _last(text).words[-1]
    assert (last.is_filler, last.reason) == (None, PENDING)


def test_the_pending_word_keeps_its_tier():
    assert _last("그").words[-1].tier == "T2"
    assert _last("그니까").words[-1].tier == "T3"


def test_last_t1_word_is_not_pending():
    assert _last("음").words[-1].is_filler is True


def test_last_word_not_in_the_dictionary_is_not_pending():
    assert _last("분기").words[-1].is_filler is False


def test_pending_word_is_decided_when_the_next_word_arrives():
    words = [word("매출은", 50300, 50800), word("그", 51500, 51650), word("좋아요", 51700, 52100)]
    result = run(53000, words)
    assert verdicts(result) == [False, True, False]
    assert result.words[1].reason == "앞 간격이 기준 이상인 단독 단어"


def test_pending_word_is_decided_as_not_a_filler_when_the_next_word_comes_quickly():
    words = [word("매출은", 51400, 51450), word("그", 51500, 51650), word("좋아요", 51700, 52100)]
    assert run(53000, words).words[1].is_filler is False


def test_pending_only_happens_on_the_last_word():
    words = [word("그", 1000, 1100), word("좀", 2000, 2100), word("그", 3000, 3100)]
    assert verdicts(run(4000, words)) == [True, True, None]


def test_a_sentence_end_signal_at_the_word_end_means_the_after_gap_is_big_enough():
    # UtteranceEnd 는 단어 끝 시각과 같고 그 뒤로 말이 없었다는 뜻이다
    last = _last("그", utterance_ends=[51650]).words[-1]
    assert (last.is_filler, last.reason) == (True, "앞뒤 간격이 기준 이상인 단독 단어")


def test_a_signal_at_the_word_end_decides_a_t2_word_with_a_short_before_gap():
    words = [word(PLAIN, 1000, 1500), word("그", 1550, 1650)]
    last = run(3000, words, utterance_ends=[1650]).words[-1]
    assert (last.is_filler, last.reason) == (True, "뒤 간격이 기준 이상인 단독 단어")


def test_a_signal_at_the_word_end_decides_a_t3_word_only_with_a_big_before_gap():
    short = run(3000, [word(PLAIN, 1000, 1500), word("좀", 1550, 1650)], utterance_ends=[1650])
    big = run(3000, [word(PLAIN, 1000, 1500), word("좀", 1900, 2000)], utterance_ends=[2000])
    assert short.words[-1].is_filler is False
    assert big.words[-1].is_filler is True


def test_a_signal_after_the_word_end_gives_the_after_gap_by_the_difference():
    words = [word(PLAIN, 1000, 1500), word("그", 1550, 1650)]
    below = run(3000, words, utterance_ends=[1650 + 399]).words[-1]
    at = run(3000, words, utterance_ends=[1650 + 400]).words[-1]
    assert below.is_filler is False
    assert (at.is_filler, at.reason) == (True, "뒤 간격이 기준 이상인 단독 단어")


def test_the_earliest_signal_after_the_word_is_used():
    words = [word(PLAIN, 1000, 1500), word("그", 1550, 1650)]
    result = run(3000, words, utterance_ends=[1700, 2500])
    assert result.words[-1].is_filler is False  # 간격 50


def test_a_signal_before_the_word_end_does_not_decide_it():
    words = [word(PLAIN, 1000, 1500), word("그", 1550, 1650)]
    assert run(3000, words, utterance_ends=[1600]).words[-1].is_filler is None


def test_take_end_decides_the_last_word_by_the_time_left_after_it():
    result = _last("그", take_end_ms=51650 + 400)
    assert result.words[-1].is_filler is True
    assert result.words[-1].reason == "앞뒤 간격이 기준 이상인 단독 단어"


def test_take_end_gives_the_after_gap_for_a_t2_word_with_a_short_before_gap():
    words = [word(PLAIN, 1000, 1500), word("그", 1550, 1650)]
    last = run(3000, words, take_end_ms=1650 + 400).words[-1]
    assert (last.is_filler, last.reason) == (True, "뒤 간격이 기준 이상인 단독 단어")
    assert run(3000, words, take_end_ms=1650 + 399).words[-1].is_filler is False


def test_take_end_equal_to_the_word_end_makes_the_after_gap_zero_so_it_is_not_a_filler():
    words = [word(PLAIN, 1000, 1500), word("그", 1550, 1650)]
    last = run(3000, words, take_end_ms=1650).words[-1]
    assert (last.is_filler, last.reason) == (False, "간격이 기준보다 짧음")


@pytest.mark.parametrize(("take_end_ms", "expected"), [(2000, False), (2399, False), (2400, True)])
def test_take_end_gap_after_a_t3_word_is_judged_at_the_threshold(take_end_ms, expected):
    # "좀"(T3)은 앞 간격 400 이므로 뒤 간격(take_end_ms − 2000)이 400 이상일 때만 군더더기다
    words = [word(PLAIN, 1000, 1500), word("좀", 1900, 2000)]
    assert run(3000, words, take_end_ms=take_end_ms).words[-1].is_filler is expected


def test_take_end_leaves_no_pending_word():
    words = [word("그", 1000, 1100), word("좀", 2000, 2100), word("그니까", 3000, 3100)]
    assert None not in verdicts(run(4000, words, take_end_ms=3100))


def test_a_signal_is_used_before_take_end():
    words = [word(PLAIN, 1000, 1500), word("그", 1550, 1650)]
    last = run(3000, words, utterance_ends=[1650], take_end_ms=1650).words[-1]
    assert last.is_filler is True
