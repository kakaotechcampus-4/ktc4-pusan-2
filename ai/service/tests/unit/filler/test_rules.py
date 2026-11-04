"""단어별 판정 규칙 — 층별 조건, 간격 경계, 바로 반복, 앞 간격, 문장부호 (filler.md 5-2 · 6-1)."""

from __future__ import annotations

import pytest

from pitch_coach_ai.features.filler.core import _before, normalize
from pitch_coach_ai.features.filler.schemas import Word

from .helpers import CFG, PLAIN, gap_case, inputs, run, verdicts, word

GAP = CFG.gap_ms

# ── 사전 ───────────────────────────────────────────────────────────────────────


def test_word_not_in_the_dictionary_is_not_a_filler():
    [w] = run(2000, [word("매출은", 1000, 1500)]).words
    assert (w.tier, w.is_filler, w.reason) == (None, False, "사전에 없음")


def test_output_word_keeps_the_input_text_as_it_was():
    [w] = run(2000, [word("어어.", 1000, 1500)]).words
    assert w.word == "어어."


def test_trailing_punctuation_is_stripped_before_matching_the_dictionary():
    [w] = run(2000, [word("어어.", 1000, 1500)]).words
    assert (w.tier, w.is_filler, w.reason) == ("T1", True, "채움말")


def test_surrounding_spaces_and_unicode_punctuation_are_stripped():
    assert normalize(" “음…” ") == "음"


def test_a_word_wrapped_in_spaces_and_quotes_is_a_t1_filler():
    [w] = run(2000, [word(" “음…” ", 1000, 1500)]).words
    assert (w.tier, w.is_filler, w.reason) == ("T1", True, "채움말")


@pytest.mark.parametrize("text", ["+음+", "😀음😀"])
def test_symbols_are_not_punctuation_so_they_are_not_stripped(text):
    # 문장부호(유니코드 P)가 아닌 기호는 떼지 않으므로 사전 밖 단어가 된다
    [w] = run(2000, [word(text, 1000, 1500)]).words
    assert (w.tier, w.is_filler, w.reason) == (None, False, "사전에 없음")


def test_punctuation_in_the_middle_of_a_word_stays():
    [w] = run(2000, [word("어-음", 1000, 1500)]).words
    assert (w.tier, w.is_filler) == (None, False)


# ── T1 ────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("text", CFG.t1_words)
def test_every_t1_word_is_a_filler_even_with_short_gaps(text):
    result = gap_case(text, before=10, after=10)
    assert result.words[1].tier == "T1" and result.words[1].is_filler is True
    assert result.words[1].reason == "채움말"


def test_t1_word_is_a_filler_even_as_the_last_word():
    [w] = run(2000, [word("음", 1000, 1300)]).words
    assert w.is_filler is True


def test_repeated_t1_words_are_all_fillers():
    words = [word("음", 1000, 1300), word("음", 1310, 1600), word("음", 1610, 1900)]
    assert verdicts(run(3000, words)) == [True, True, True]


# ── T2 ────────────────────────────────────────────────────────────────────────


def test_t2_word_with_short_gaps_on_both_sides_is_not_a_filler():
    [_, w, _] = gap_case("그", before=GAP - 1, after=GAP - 1).words
    assert (w.tier, w.is_filler, w.reason) == ("T2", False, "간격이 기준보다 짧음")


def test_t2_word_with_both_gaps_at_the_threshold_is_a_standalone_word():
    [_, w, _] = gap_case("그", before=GAP, after=GAP).words
    assert (w.is_filler, w.reason) == (True, "앞뒤 간격이 기준 이상인 단독 단어")


def test_t2_word_with_only_the_before_gap_at_the_threshold_is_a_filler():
    [_, w, _] = gap_case("그", before=GAP, after=GAP - 1).words
    assert (w.is_filler, w.reason) == (True, "앞 간격이 기준 이상인 단독 단어")


def test_t2_word_with_only_the_after_gap_at_the_threshold_is_a_filler():
    [_, w, _] = gap_case("그", before=GAP - 1, after=GAP).words
    assert (w.is_filler, w.reason) == (True, "뒤 간격이 기준 이상인 단독 단어")


def test_t2_word_one_ms_below_the_threshold_on_both_sides_is_not_a_filler():
    assert gap_case("저", before=GAP - 1, after=GAP - 1).words[1].is_filler is False


@pytest.mark.parametrize("text", CFG.t2_words)
def test_every_t2_word_is_judged_by_gaps(text):
    result = gap_case(text, before=GAP, after=GAP)
    assert result.words[1].tier == "T2" and result.words[1].is_filler is True


# ── T3 ────────────────────────────────────────────────────────────────────────


def test_t3_word_with_both_gaps_at_the_threshold_is_a_standalone_word():
    [_, w, _] = gap_case("그니까", before=GAP, after=GAP).words
    assert (w.tier, w.is_filler, w.reason) == ("T3", True, "앞뒤 간격이 기준 이상인 단독 단어")


def test_t3_word_needs_both_gaps_so_only_the_before_gap_is_not_enough():
    [_, w, _] = gap_case("그니까", before=5_000, after=GAP - 1).words
    assert (w.is_filler, w.reason) == (False, "간격이 기준보다 짧음")


def test_t3_word_needs_both_gaps_so_only_the_after_gap_is_not_enough():
    [_, w, _] = gap_case("그니까", before=GAP - 1, after=5_000).words
    assert (w.is_filler, w.reason) == (False, "간격이 기준보다 짧음")


@pytest.mark.parametrize("text", CFG.t3_words)
def test_every_t3_word_is_judged_by_both_gaps(text):
    result = gap_case(text, before=GAP, after=GAP)
    assert result.words[1].tier == "T3" and result.words[1].is_filler is True


# ── 바로 반복 ──────────────────────────────────────────────────────────────────


def test_a_t2_word_repeated_right_away_counts_all_but_the_last_one():
    words = [
        word(PLAIN, 0, 500),
        word("그", 600, 700),
        word("그", 750, 850),
        word("사람", 900, 1300),
    ]
    result = run(2000, words)
    assert verdicts(result) == [False, True, False, False]
    assert result.words[1].reason == "같은 단어가 바로 반복"
    assert result.words[2].reason == "간격이 기준보다 짧음"


def test_the_last_of_repeated_words_is_judged_by_gaps_not_by_repetition():
    words = [
        word(PLAIN, 0, 500),
        word("그", 600, 700),
        word("그", 750, 850),
        word(PLAIN, 1300, 1600),
    ]
    # 마지막 "그" 는 뒤 간격 450 이 기준 이상이라 간격 규칙으로 군더더기가 된다
    result = run(2000, words)
    assert result.words[2].reason == "뒤 간격이 기준 이상인 단독 단어"


def test_three_repeated_t2_words_count_the_first_two_by_repetition():
    words = [word("그", 0, 100), word("그", 150, 250), word("그", 300, 400), word(PLAIN, 450, 800)]
    assert verdicts(run(2000, words)) == [True, True, False, False]


def test_a_t3_word_repeated_right_away_is_a_filler_even_with_short_gaps():
    words = [
        word(PLAIN, 0, 500),
        word("좀", 550, 700),
        word("좀", 750, 900),
        word(PLAIN, 950, 1300),
    ]
    result = run(2000, words)
    assert (result.words[1].is_filler, result.words[1].reason) == (True, "같은 단어가 바로 반복")
    assert result.words[2].is_filler is False


def test_repetition_ignores_punctuation_on_the_words():
    words = [word("그,", 0, 100), word("그", 150, 250), word(PLAIN, 300, 700)]
    assert verdicts(run(2000, words)) == [True, False, False]


def test_a_sentence_end_signal_between_the_words_breaks_the_repetition():
    words = [
        word(PLAIN, 0, 300),
        word("그", 400, 500),
        word("그", 600, 700),
        word(PLAIN, 750, 1100),
    ]
    result = run(2000, words, utterance_ends=[550])
    assert result.words[1].is_filler is False
    assert result.words[1].reason == "간격이 기준보다 짧음"


def test_different_dictionary_words_in_a_row_are_not_a_repetition():
    words = [
        word(PLAIN, 0, 500),
        word("그", 550, 650),
        word("저", 700, 800),
        word(PLAIN, 850, 1200),
    ]
    assert verdicts(run(2000, words)) == [False, False, False, False]


# ── 창의 첫 단어의 앞 간격 ──────────────────────────────────────────────────────


def test_first_word_before_gap_is_measured_from_the_sentence_end_signal():
    words = [word("그", 1500, 1600), word("사람", 1700, 2100)]
    result = run(3000, words, utterance_ends=[1000])
    assert result.words[0].reason == "앞 간격이 기준 이상인 단독 단어"


def test_first_word_uses_the_latest_signal_before_it():
    words = [word("그", 1500, 1600), word("사람", 1700, 2100)]
    result = run(3000, words, utterance_ends=[500, 1300])
    assert result.words[0].is_filler is False  # 간격 200


def test_first_word_before_gap_is_unknown_without_a_signal_so_it_does_not_count():
    words = [word("그", 1500, 1600), word("사람", 1700, 2100)]
    assert run(3000, words).words[0].is_filler is False


def test_a_signal_older_than_stt_ok_since_is_not_used_for_the_first_word():
    words = [word("그", 6500, 6600), word("사람", 6700, 7100)]
    result = run(8000, words, utterance_ends=[1000], stt_ok_since_ms=6000)
    assert result.words[0].is_filler is False


def test_a_signal_after_the_first_word_starts_does_not_give_a_before_gap():
    words = [word("그", 1500, 1600), word("사람", 1700, 2100)]
    assert run(3000, words, utterance_ends=[1600]).words[0].is_filler is False


def test_words_before_stt_ok_since_are_dropped_from_the_window():
    words = [word("음", 500, 800), word("그", 6500, 6600), word("사람", 6700, 7100)]
    result = run(8000, words, stt_ok_since_ms=6000)
    assert [w.word for w in result.words] == ["그", "사람"]


# ── 문장 첫 단어 ───────────────────────────────────────────────────────────────


def _ws(*spans: tuple[int, int]) -> list[Word]:
    """(start_ms, end_ms) 마다 사전 밖 단어를 만든다. 아래 `_before` 직접 테스트용."""
    return [Word(word=PLAIN, start_ms=s, end_ms=e) for s, e in spans]


def test_a_word_after_a_sentence_end_signal_is_a_sentence_start():
    """공개 출력으로 볼 수 없는 문장 첫 단어 여부를 보는 내부 테스트."""
    ws = _ws((0, 500), (700, 900))
    assert _before(ws, 1, [600], 0, CFG) == (200, True)


def test_a_word_after_a_gap_at_the_sentence_threshold_is_a_sentence_start():
    """공개 출력으로 볼 수 없는 문장 첫 단어 여부를 보는 내부 테스트."""
    ws = _ws((0, 500), (1500, 1700))
    assert _before(ws, 1, [], 0, CFG) == (1000, True)


def test_a_word_after_a_shorter_gap_is_not_a_sentence_start():
    """공개 출력으로 볼 수 없는 문장 첫 단어 여부를 보는 내부 테스트."""
    ws = _ws((0, 500), (1499, 1700))
    assert _before(ws, 1, [], 0, CFG) == (999, False)


def test_the_first_word_is_a_sentence_start_only_when_a_signal_precedes_it():
    """공개 출력으로 볼 수 없는 문장 첫 단어 여부를 보는 내부 테스트."""
    ws = _ws((1500, 1700))
    assert _before(ws, 0, [], 0, CFG) == (None, False)
    assert _before(ws, 0, [1200], 0, CFG) == (300, True)
    assert _before(ws, 0, [1200], 1300, CFG) == (None, False)


def test_a_sentence_start_with_a_short_after_gap_is_not_a_filler():
    # 문장 첫 단어 조건은 뒤 간격이 기준 이상일 때만 군더더기로 만든다
    words = [word(PLAIN, 0, 500), word("그", 700, 800), word(PLAIN, 850, 1200)]
    result = run(2000, words, utterance_ends=[600])
    assert result.words[1].is_filler is False


# ── 입력 정리 ──────────────────────────────────────────────────────────────────


def test_a_word_just_before_stt_ok_since_is_dropped_and_one_at_it_is_kept():
    # 6000 이전에 시작한 단어는 출력에서 빠지고, 6000 에 시작한 단어는 들어간다
    before = run(8000, [word("음", 5999, 6300)], stt_ok_since_ms=6000)
    assert before.words == []
    at = run(8000, [word("음", 6000, 6300)], stt_ok_since_ms=6000)
    assert [w.word for w in at.words] == ["음"]


def test_unsorted_input_words_are_judged_in_time_order():
    words = [word("사람", 1700, 2100), word("그", 1500, 1600)]
    result = run(3000, words, utterance_ends=[1000])
    assert [w.word for w in result.words] == ["그", "사람"]


def test_no_words_gives_an_empty_word_list():
    assert run(3000).words == []


def test_inputs_helper_builds_a_valid_request():
    assert inputs(1000)["t_ms"] == 1000
