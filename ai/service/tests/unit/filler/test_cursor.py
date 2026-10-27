"""새 몫 세기와 두 커서 — 보류 앞에서 멈춤, 겹친 창, 빠진 요청, 이상한 단어 (filler.md 6-1)."""

from __future__ import annotations

import pytest

from pitch_coach_ai.features.filler.core import judge

from .helpers import (
    CFG,
    PLAIN,
    inputs,
    run,
    time_items,
    total,
    verdicts,
    word,
    word_items,
)

# ── 단어 커서 ──────────────────────────────────────────────────────────────────


def test_word_cursor_stops_before_a_pending_word():
    words = [word(PLAIN, 100, 500), word("음", 1000, 1300), word("그", 2000, 2100)]
    result = run(3000, words)
    assert verdicts(result) == [False, True, None]
    assert result.words_counted_until_ms == 1300


def test_word_cursor_is_the_largest_end_of_the_decided_words():
    words = [word(PLAIN, 100, 500), word("음", 1000, 1300), word(PLAIN, 1500, 2000)]
    assert run(3000, words).words_counted_until_ms == 2000


def test_word_cursor_stays_when_the_only_new_word_is_pending():
    words = [word("매출은", 50300, 50800), word("그", 51500, 51650)]
    assert run(52000, words, words_since_ms=50800).words_counted_until_ms == 50800


def test_word_cursor_stays_as_received_when_there_are_no_new_words():
    words = [word("음", 1000, 1300), word(PLAIN, 1500, 2000)]
    result = run(5000, words, words_since_ms=2000)
    assert result.words_counted_until_ms == 2000
    assert word_items(result) == []


def test_word_cursor_stays_as_received_even_when_the_words_are_all_older():
    result = run(9000, [word(PLAIN, 100, 500)], words_since_ms=7000)
    assert result.words_counted_until_ms == 7000


def test_an_earlier_word_does_not_move_the_cursor_back():
    # 시각이 이른 단어(end 6000)가 와도 받은 커서(7000)보다 작아지지 않는다
    words = [word("음", 5700, 6000)]
    result = run(9000, words, words_since_ms=7000)
    assert result.words_counted_until_ms == 7000
    assert word_items(result) == []


def test_same_input_gives_the_same_result():
    words = [word("음", 1000, 1300), word("그", 2000, 2100)]
    inp = inputs(3000, words)
    assert judge(inp, 3000, CFG) == judge(inp, 3000, CFG)


# ── 새 몫 ─────────────────────────────────────────────────────────────────────


def test_only_new_filler_words_are_counted():
    words = [word("음", 1000, 1300), word("어", 2000, 2300), word("에", 3000, 3300)]
    result = run(4000, words, words_since_ms=2300)
    assert [i.t_ms for i in word_items(result)] == [3000]


def test_a_new_filler_adds_count_tier_and_word_keys_at_its_start_time():
    [item] = word_items(
        run(2000, [word("그니까", 1000, 1300)], utterance_ends=[300], take_end_ms=2000)
    )
    assert item.t_ms == 1000
    assert item.values == {"filler_count": 1, "filler_t3": 1, "filler_word:그니까": 1}


def test_the_word_key_uses_the_word_without_punctuation():
    [item] = word_items(run(2000, [word("어어.", 1000, 1300)]))
    assert item.values == {"filler_count": 1, "filler_t1": 1, "filler_word:어어": 1}


def test_a_word_that_is_not_a_filler_is_not_counted():
    result = run(2000, [word(PLAIN, 1000, 1300)])
    assert word_items(result) == []


def test_a_filler_decided_later_is_counted_once_in_the_request_that_decides_it():
    first = run(3000, [word(PLAIN, 100, 500), word("그", 1500, 1700)])
    assert total(first.tally, "filler_count") == 0
    words = [word(PLAIN, 100, 500), word("그", 1500, 1700), word("좋아요", 1800, 2300)]
    second = run(4000, words, since_ms=3000, words_since_ms=first.words_counted_until_ms)
    assert total(second.tally, "filler_count") == 1
    third = run(5000, words, since_ms=4000, words_since_ms=second.words_counted_until_ms)
    assert total(third.tally, "filler_count") == 0


def test_a_word_overlapping_the_already_counted_range_is_dropped_and_not_counted():
    # 센 구간(커서 1200)에 걸쳐 시작한 새 단어는 이미 센 단어와 겹친 것이다
    result = run(3000, [word("음", 1000, 1500)], words_since_ms=1200)
    assert result.words == []
    assert word_items(result) == []
    assert result.words_counted_until_ms == 1500


# ── 겹친 창을 다시 부르기 ───────────────────────────────────────────────────────

STREAM = [
    word("분기", 800, 1200),
    word("음", 2000, 2300),
    word("그니까", 4000, 4400),
    word("매출은", 5000, 5500),
    word("그", 6600, 6700),
    word("그", 6750, 6850),
    word("사람이", 6900, 7300),
    word("음", 8000, 8300),
    word("어", 8400, 8700),
    word("좀", 10_000, 10_200),
]
END = 12_000


def _chain(step_ms: int = 1_000):
    """1초(또는 step_ms)마다 확정된 단어를 겹쳐 보내며 두 커서를 이어 부른다. 마지막은 Take 끝."""
    since, words_since = 0, 0
    results = []
    t = step_ms
    while t <= END:
        words = [w for w in STREAM if w["end_ms"] <= t]
        extra = {"take_end_ms": END} if t == END else {}
        r = run(t, words, since_ms=since, words_since_ms=words_since, **extra)
        since, words_since = r.counted_until_ms, r.words_counted_until_ms
        results.append(r)
        t += step_ms
    return results


def _sum(results, key):
    return sum(total(r.tally, key) for r in results)


def test_chained_requests_count_the_same_as_one_request():
    chained = _chain()
    once = run(END, STREAM, take_end_ms=END)
    for key in ("filler_count", "filler_t1", "filler_t2", "filler_t3", "total_ms", "elapsed_ms"):
        assert _sum(chained, key) == total(once.tally, key), key
    for key in ("filler_word:음", "filler_word:그니까", "filler_word:그", "filler_word:어"):
        assert _sum(chained, key) == total(once.tally, key), key


def test_chained_requests_count_each_filler_word_once():
    chained = _chain()
    starts = sorted(i.t_ms for r in chained for i in word_items(r))
    assert len(starts) == len(set(starts))
    assert starts == sorted({i.t_ms for i in word_items(run(END, STREAM, take_end_ms=END))})


def test_chained_requests_with_coarser_steps_count_the_same():
    assert _sum(_chain(3_000), "filler_count") == _sum(_chain(1_000), "filler_count")


def test_calling_again_with_the_returned_cursors_counts_nothing_new():
    first = run(END, STREAM, take_end_ms=END)
    again = run(
        END + 1_000,
        STREAM,
        since_ms=first.counted_until_ms,
        words_since_ms=first.words_counted_until_ms,
        take_end_ms=END + 1_000,
    )
    assert word_items(again) == []


# ── 시간 몫 ───────────────────────────────────────────────────────────────────


def test_time_share_is_one_item_per_second_from_the_time_cursor():
    result = run(3000, since_ms=2000)
    assert [(i.t_ms, i.values) for i in time_items(result)] == [
        (2000, {"elapsed_ms": 1000, "total_ms": 1000})
    ]


def test_missed_requests_give_one_time_item_per_second_and_a_short_last_piece():
    result = run(3500, since_ms=0)
    assert [(i.t_ms, i.values["total_ms"]) for i in time_items(result)] == [
        (0, 1000),
        (1000, 1000),
        (2000, 1000),
        (3000, 500),
    ]


def test_no_time_item_when_the_time_cursor_is_at_or_after_t():
    assert time_items(run(3000, since_ms=3000)) == []
    assert run(3000, since_ms=4000).counted_until_ms == 4000


def test_elapsed_time_counts_only_the_part_after_stt_ok_since():
    result = run(3000, since_ms=0, stt_ok_since_ms=1500)
    assert [(i.t_ms, i.values) for i in time_items(result)] == [
        (0, {"total_ms": 1000}),
        (1000, {"elapsed_ms": 500, "total_ms": 1000}),
        (2000, {"elapsed_ms": 1000, "total_ms": 1000}),
    ]


def test_words_before_stt_ok_since_are_not_counted():
    words = [word("음", 500, 800), word("어", 6500, 6800)]
    result = run(8000, words, stt_ok_since_ms=6000)
    assert [i.t_ms for i in word_items(result)] == [6500]


def test_a_word_starting_at_stt_ok_since_is_counted_and_one_just_before_it_is_not():
    before = run(8000, [word("음", 5999, 6300)], stt_ok_since_ms=6000)
    assert word_items(before) == []
    at = run(8000, [word("음", 6000, 6300)], stt_ok_since_ms=6000)
    assert total(at.tally, "filler_count") == 1


def test_word_items_come_before_time_items():
    result = run(3000, [word("음", 500, 800)])
    kinds = ["time" if "total_ms" in i.values else "word" for i in result.tally]
    assert kinds == sorted(kinds, key=lambda k: k == "time")


# ── 이상한 단어 ────────────────────────────────────────────────────────────────


def test_a_word_with_reversed_times_is_dropped_but_moves_the_cursor():
    words = [word("음", 3000, 2000)]
    result = run(5000, words, words_since_ms=1500)
    assert result.words == []
    assert word_items(result) == []
    assert result.words_counted_until_ms == 2000


def test_an_overlapping_word_is_dropped_but_moves_the_cursor():
    words = [word(PLAIN, 1000, 1500), word("음", 1400, 1800)]
    result = run(5000, words)
    assert [w.word for w in result.words] == [PLAIN]
    assert word_items(result) == []
    assert result.words_counted_until_ms == 1800


def test_a_dropped_word_does_not_break_the_neighbours_gap_judgement():
    words = [
        word(PLAIN, 1000, 1500),
        word("음", 1400, 1800),
        word("그", 2000, 2100),
        word(PLAIN, 2200, 2500),
    ]
    result = run(5000, words)
    assert [w.word for w in result.words] == [PLAIN, "그", PLAIN]
    # 앞 간격은 뺀 단어(끝 1800)가 아니라 앞 단어(끝 1500)로 잰 500 ms 다(뺀 단어로 재면 200 ms)
    target = result.words[1]
    assert (target.is_filler, target.reason) == (True, "앞 간격이 기준 이상인 단독 단어")


def test_a_pending_word_stops_the_cursor_even_after_dropped_words():
    words = [word(PLAIN, 1000, 1500), word("음", 1400, 1800), word("그", 2000, 2100)]
    result = run(5000, words)
    assert verdicts(result) == [False, None]
    assert result.words_counted_until_ms == 1800


def test_a_dropped_word_ending_after_the_pending_word_does_not_push_the_cursor_past_it():
    # 겹친 "오류"(끝 1000)가 보류 단어 "그"(끝 200)보다 늦게 끝나도 커서는 100 에 머문다
    words = [word("내용", 0, 100), word("오류", 50, 1000), word("그", 110, 200)]
    first = run(3000, words, since_ms=0)
    assert verdicts(first) == [False, None]
    assert first.words_counted_until_ms == 100
    more = [*words, word("다음", 1200, 1300)]
    second = run(4000, more, since_ms=3000, words_since_ms=first.words_counted_until_ms)
    assert verdicts(second) == [False, True, False]
    assert second.words_counted_until_ms == 1300
    once = run(4000, more)
    # 나눠 불러도 한 번에 부른 것과 같게 "그" 가 한 번 센다
    assert total(first.tally, "filler_count") + total(second.tally, "filler_count") == 1
    assert total(once.tally, "filler_count") == 1
    assert word_items(first) == []
    assert [i.t_ms for i in word_items(second)] == [110]


@pytest.mark.parametrize("text", ["그", "좀"])
def test_a_pending_word_decided_as_not_a_filler_does_not_come_back_with_the_returned_cursor(text):
    first = run(3000, [word(PLAIN, 1000, 1500), word(text, 1550, 1650)])
    assert verdicts(first)[-1] is None
    assert first.words_counted_until_ms == 1500
    words = [word(PLAIN, 1000, 1500), word(text, 1550, 1650), word(PLAIN, 1700, 2100)]
    second = run(4000, words, since_ms=3000, words_since_ms=first.words_counted_until_ms)
    assert [w.is_filler for w in second.words][1] is False
    assert word_items(second) == []
    assert second.words_counted_until_ms == 2100
    # 돌려받은 커서로 같은 창을 다시 불러도 그 단어는 새 단어로 돌아오지 않는다
    third = run(5000, words, since_ms=4000, words_since_ms=second.words_counted_until_ms)
    assert word_items(third) == []
    assert third.words_counted_until_ms == 2100
