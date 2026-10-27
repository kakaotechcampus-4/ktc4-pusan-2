"""세는 층(`counted_tiers`) — 배포 기본값은 T1만 센다 (filler.md 5-2 · 6-1).

문장 끝 신호(1초 넘게 쉼) 뒤 첫 단어는 앞 간격이 늘 기준보다 커서, T2 간격 규칙은 문장 첫
지시어("그 결과" · "자 이제")를 군더더기로 센다. 그래서 배포 기본값(`DEFAULT`)은 T1만 세고,
T2 · T3는 층 · 이유만 판정한다.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from pitch_coach_ai.features.filler.config import ALL_TIERS, DEFAULT, FillerConfig
from pitch_coach_ai.features.filler.core import criteria_version, judge

from .helpers import CFG, PLAIN, inputs, run, word

NOT_COUNTED = "세지 않는 층"


def _only(tiers: tuple[str, ...]) -> FillerConfig:
    """규칙 테스트와 같은 기준값에서 세는 층만 바꾼 설정."""
    return CFG.model_copy(update={"counted_tiers": tiers})


def test_default_counts_only_t1():
    assert DEFAULT.counted_tiers == ("T1",)
    assert ALL_TIERS.counted_tiers == ("T1", "T2", "T3")


def _sentence_start_demonstrative(config: FillerConfig):
    # 문장 끝(10500) 1.2초 뒤 "그 결과 매출이"
    words = [
        word(PLAIN, 10_000, 10_500),
        word("그", 11_700, 11_800),
        word("결과", 11_850, 12_100),
        word("매출이", 12_150, 12_500),
    ]
    return run(15_000, words, config=config, utterance_ends=[10_500]).words[1]


def _mid_sentence_demonstrative(config: FillerConfig):
    # 문장 중간 0.5초 쉼 뒤 "이 그래프를 보면"
    words = [
        word(PLAIN, 10_000, 10_400),
        word("이", 10_900, 11_000),
        word("그래프를", 11_050, 11_400),
        word("보면", 11_450, 11_700),
    ]
    return run(15_000, words, config=config).words[1]


@pytest.mark.parametrize("case", [_sentence_start_demonstrative, _mid_sentence_demonstrative])
def test_a_demonstrative_after_a_pause_is_not_counted_by_default(case):
    w = case(DEFAULT)
    assert (w.tier, w.is_filler, w.reason) == ("T2", False, NOT_COUNTED)


@pytest.mark.parametrize("case", [_sentence_start_demonstrative, _mid_sentence_demonstrative])
def test_the_same_demonstrative_is_counted_when_t2_is_turned_on(case):
    # T2 를 켜면 지금 간격 규칙(앞이나 뒤 간격이 기준 이상)대로 센다
    assert case(ALL_TIERS).is_filler is True


def test_a_last_t2_word_is_not_pending_by_default_and_the_cursor_passes_it():
    words = [word(PLAIN, 1_000, 1_500), word("그", 1_550, 1_650)]
    result = run(3_000, words, config=DEFAULT)
    assert (result.words[1].is_filler, result.words[1].reason) == (False, NOT_COUNTED)
    assert result.words_counted_until_ms == 1_650


def test_a_repeated_t3_word_is_not_counted_by_default():
    words = [word("진짜", 1_000, 1_300), word("진짜", 1_310, 1_600), word(PLAIN, 1_610, 2_000)]
    result = run(3_000, words, config=DEFAULT)
    assert [w.is_filler for w in result.words] == [False, False, False]
    assert not any("filler_count" in item.values for item in result.tally)


def test_a_t1_word_is_still_counted_by_default():
    result = run(3_000, [word(PLAIN, 1_000, 1_500), word("음", 1_600, 1_900)], config=DEFAULT)
    assert result.words[1].is_filler is True
    [item] = [i for i in result.tally if "filler_count" in i.values]
    assert item.values == {"filler_count": 1, "filler_t1": 1, "filler_word:음": 1}


def test_judge_without_a_config_uses_the_t1_only_default():
    # 서비스는 설정 없이 부른다: 기본 인자가 T1만 세는 DEFAULT 여야 한다
    words = [word(PLAIN, 10_000, 10_500), word("그", 11_700, 11_800), word("음", 12_300, 12_600)]
    inp = inputs(15_000, words, utterance_ends=[10_500])
    [result] = judge(inp, 15_000)
    assert [(w.word, w.is_filler) for w in result.words] == [
        (PLAIN, False),
        ("그", False),
        ("음", True),
    ]
    assert result.criteria_version == criteria_version(DEFAULT)


@pytest.mark.parametrize("tiers", [("T2",), ("T3",)])
def test_t1_is_not_counted_when_it_is_left_out_of_counted_tiers(tiers):
    result = run(3_000, [word(PLAIN, 1_000, 1_500), word("음", 1_600, 1_900)], config=_only(tiers))
    assert (result.words[1].is_filler, result.words[1].reason) == (False, NOT_COUNTED)
    assert not any("filler_count" in item.values for item in result.tally)


def test_only_the_turned_on_tier_follows_its_gap_rule():
    # T2 만 켜면 T2 는 간격 규칙대로 세고, T3 는 같은 쉼이어도 세지 않는다
    words = [
        word(PLAIN, 1_000, 1_500),
        word("그", 2_000, 2_100),
        word(PLAIN, 2_600, 3_000),
        word("진짜", 3_500, 3_800),
        word(PLAIN, 4_300, 4_700),
    ]
    result = run(6_000, words, config=_only(("T2",)))
    assert (result.words[1].is_filler, result.words[1].reason) == (
        True,
        "앞뒤 간격이 기준 이상인 단독 단어",
    )
    assert (result.words[3].is_filler, result.words[3].reason) == (False, NOT_COUNTED)


def test_a_last_t3_word_is_not_pending_and_a_later_t1_is_counted_once():
    first_words = [word(PLAIN, 1_000, 1_500), word("그니까", 1_550, 1_900)]
    first = run(3_000, first_words, config=DEFAULT, since_ms=2_000)
    assert (first.words[1].is_filler, first.words[1].reason) == (False, NOT_COUNTED)
    assert first.words_counted_until_ms == 1_900
    more = [*first_words, word("음", 3_100, 3_400)]
    second = run(
        5_000,
        more,
        config=DEFAULT,
        since_ms=first.counted_until_ms,
        words_since_ms=first.words_counted_until_ms,
    )
    counted = [i for i in second.tally if "filler_count" in i.values]
    assert [i.t_ms for i in counted] == [3_100]
    # 돌려받은 커서로 같은 창을 다시 부르면 이미 센 "음"은 다시 나오지 않는다
    third = run(
        6_000,
        more,
        config=DEFAULT,
        since_ms=second.counted_until_ms,
        words_since_ms=second.words_counted_until_ms,
    )
    assert not any("filler_count" in i.values for i in third.tally)


def test_take_end_does_not_turn_an_uncounted_last_word_into_a_filler():
    words = [word(PLAIN, 1_000, 1_500), word("그", 2_000, 2_100)]
    result = run(5_000, words, config=DEFAULT, take_end_ms=5_000)
    assert (result.words[1].is_filler, result.words[1].reason) == (False, NOT_COUNTED)


@pytest.mark.parametrize("tiers", [(), ("T1", "T1")])
def test_counted_tiers_must_be_non_empty_without_duplicates(tiers):
    with pytest.raises(ValidationError):
        FillerConfig(counted_tiers=tiers)
