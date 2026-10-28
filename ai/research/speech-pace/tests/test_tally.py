"""tally 와 커서 — 새 단어를 한 번씩만 세고, 보류 · 멈춤 · 시간 몫을 나눈다.

pace.md 5-3 · 6-1 · 7절, architecture.md 7절 '커서'.
"""

from __future__ import annotations

from typing import Any

from pace.schemas import TallyItem

from .helpers import run, time_items, total, word, word_items

# ── 단어 몫 ────────────────────────────────────────────────────────────────────


def test_each_new_word_adds_its_chars_and_speaking_time_at_its_start():
    words = [word("지난", 1000, 1300), word("분기", 1500, 1900), word("매출은", 2000, 2500)]
    r = run(3000, words)
    assert word_items(r) == [
        TallyItem(t_ms=1000, values={"chars": 2, "speak_ms": 300}),
        TallyItem(t_ms=1500, values={"chars": 2, "speak_ms": 400}),
        TallyItem(t_ms=2000, values={"chars": 3, "speak_ms": 500}),
    ]
    assert r.words_counted_until_ms == 2500


def test_chars_leave_out_spaces_and_punctuation():
    words = [word("매출은,", 1000, 1500), word("12%", 1600, 2000), word("A/B 테스트.", 2100, 2800)]
    assert [i.values["chars"] for i in word_items(run(3000, words))] == [3, 2, 5]


def test_a_filler_word_adds_neither_chars_nor_speaking_time():
    words = [word("지난", 1000, 1300), word("음", 1400, 1700), word("분기", 1800, 2200)]
    r = run(3000, words, fillers=[{"start_ms": 1400, "end_ms": 1700, "is_filler": True}])
    assert total(r.tally, "chars") == 4 and total(r.tally, "speak_ms") == 700
    # 판정이 정해진 단어마다 몫 하나. 군더더기는 값이 모두 0 이라 빈 몫이다
    assert TallyItem(t_ms=1400, values={}) in word_items(r)
    assert r.words_counted_until_ms == 2200


# ── 커서 ───────────────────────────────────────────────────────────────────────


def test_words_up_to_the_word_cursor_are_not_counted_again():
    words = [word("지난", 1000, 1300), word("분기", 1500, 1900), word("매출은", 2000, 2500)]
    first = run(3000, words)
    later = run(
        4000,
        [*words, word("늘었고", 3100, 3500)],
        since_ms=first.counted_until_ms,
        words_since_ms=first.words_counted_until_ms,
    )
    assert word_items(later) == [TallyItem(t_ms=3100, values={"chars": 3, "speak_ms": 400})]
    assert time_items(later) == [TallyItem(t_ms=3000, values={"ok_ms": 1000, "total_ms": 1000})]
    assert (later.counted_until_ms, later.words_counted_until_ms) == (4000, 3500)


def test_recalling_with_the_returned_cursors_counts_nothing_new():
    words = [word("지난", 1000, 1300), word("분기", 1500, 1900)]
    first = run(3000, words)
    again = run(
        3000,
        words,
        since_ms=first.counted_until_ms,
        words_since_ms=first.words_counted_until_ms,
    )
    assert again.tally == []
    assert (again.counted_until_ms, again.words_counted_until_ms) == (3000, 1900)


def test_cursors_never_go_back():
    r = run(4000, [word("지난", 1000, 1300)], since_ms=5000, words_since_ms=3000)
    assert r.tally == []
    assert (r.counted_until_ms, r.words_counted_until_ms) == (5000, 3000)


def _take_words() -> list[dict[str, Any]]:
    """80초 남짓한 Take: 3글자 단어 사이에 군더더기 "음" 이 섞이고, 2.6초를 두 번 쉰다."""
    out, t = [], 1_000
    for i in range(110):
        if i % 9 == 4:
            out.append(word("음", t, t + 300))
            t += 300 + 150
        else:
            out.append(word("발표를", t, t + 600))
            t += 600 + 120
        if i in (30, 70):
            t += 2_500
    return out


def test_overlapping_requests_with_late_words_count_every_word_exactly_once():
    """BE 처럼 1초마다 최근 60초 확정 단어를 겹쳐 보내고, 돌려받은 커서를 다음 요청에 넣는다.

    단어는 끝나고 0.8초 뒤에 확정되고, 군더더기 판정은 뒤 단어가 오기 전까지
    마지막 "음" 을 보류한다.
    """
    take = _take_words()
    since = words_since = 0
    tally: list[TallyItem] = []
    for t in range(1_000, 90_001, 1_000):
        sent = [w for w in take if t - 60_000 < w["end_ms"] <= t - 800]
        fillers = [
            {**w, "is_filler": None if w is sent[-1] else True} for w in sent if w["word"] == "음"
        ]
        r = run(t, sent, since_ms=since, words_since_ms=words_since, fillers=fillers)
        tally += r.tally
        since, words_since = r.counted_until_ms, r.words_counted_until_ms
    spoken = [w for w in take if w["word"] != "음"]
    assert sorted(i.t_ms for i in tally if "total_ms" not in i.values) == [
        w["start_ms"] for w in take
    ]
    assert total(tally, "chars") == 3 * len(spoken)
    assert total(tally, "speak_ms") == 600 * len(spoken)
    assert total(tally, "pause_count") == 2 and total(tally, "pause_ms") == 2 * 2_620
    assert total(tally, "total_ms") == total(tally, "ok_ms") == 90_000


# ── 보류 단어 ──────────────────────────────────────────────────────────────────

PENDING_WORDS = [word("지난", 1000, 1300), word("그", 1500, 1700), word("분기", 1800, 2200)]


def _mark(is_filler: bool | None) -> list[dict[str, Any]]:
    return [{"start_ms": 1500, "end_ms": 1700, "is_filler": is_filler}]


def test_a_pending_word_stops_the_word_cursor_before_it():
    r = run(3000, PENDING_WORDS, fillers=_mark(None))
    assert [i.t_ms for i in word_items(r)] == [1000]
    assert r.words_counted_until_ms == 1300


def test_a_pending_word_is_counted_once_it_is_decided():
    first = run(3000, PENDING_WORDS, fillers=_mark(None))
    cursors = {"since_ms": first.counted_until_ms, "words_since_ms": first.words_counted_until_ms}
    not_filler = run(4000, PENDING_WORDS, fillers=[], **cursors)
    assert total(not_filler.tally, "chars") == 3 and not_filler.words_counted_until_ms == 2200
    filler = run(4000, PENDING_WORDS, fillers=_mark(True), **cursors)
    assert total(filler.tally, "chars") == 2 and filler.words_counted_until_ms == 2200


def test_a_pending_word_that_is_not_counted_anyway_does_not_stop_the_cursor():
    words = [word("그", 500, 800), word("지난", 1500, 1800), word("분기", 2000, 2400)]
    marks = [{"start_ms": 500, "end_ms": 800, "is_filler": None}]
    # STT 복구 전 단어라 어차피 세지 않는다
    r = run(3000, words, fillers=marks, stt_ok_since_ms=1000)
    assert total(r.tally, "chars") == 4 and r.words_counted_until_ms == 2400
    # STT 가 ok 가 아닐 때도 같다
    r = run(3000, words, fillers=marks, stt_status="degraded")
    assert total(r.tally, "chars") == 0 and r.words_counted_until_ms == 2400


# ── 멈춤 ───────────────────────────────────────────────────────────────────────


def test_a_gap_longer_than_the_pause_minimum_is_one_pause_on_the_later_word():
    words = [
        word("가", 1000, 1300),
        word("나", 3000, 3300),  # 간격 1700 — 멈춤 아님
        word("다", 6000, 6300),  # 간격 2700 — 멈춤
        word("라", 8300, 8600),  # 간격 2000 — 기준을 넘지 않아 멈춤 아님
    ]
    r = run(9000, words)
    assert [i for i in word_items(r) if "pause_count" in i.values] == [
        TallyItem(
            t_ms=6000, values={"chars": 1, "speak_ms": 300, "pause_count": 1, "pause_ms": 2700}
        )
    ]


def test_a_pause_is_counted_only_when_the_next_word_arrives():
    first = run(9000, [word("가", 1000, 1300)])
    # 뒤에 단어가 없는 침묵은 세지 않는다
    assert total(first.tally, "pause_count") == 0
    later = run(
        10_000,
        [word("가", 1000, 1300), word("나", 9500, 9800)],
        since_ms=first.counted_until_ms,
        words_since_ms=first.words_counted_until_ms,
    )
    # 앞 단어가 지난 요청에서 센 단어여도 간격은 뒤 단어가 센다
    assert total(later.tally, "pause_count") == 1 and total(later.tally, "pause_ms") == 8200


def test_a_gap_from_a_word_before_stt_recovery_is_not_a_pause():
    words = [word("가", 1000, 1300), word("나", 4000, 4300), word("다", 7000, 7300)]
    r = run(8000, words, stt_ok_since_ms=3500)
    assert total(r.tally, "chars") == 2  # "가" 는 복구 전이라 세지 않는다
    assert r.words_counted_until_ms == 7300
    # "나" 앞 간격(2700)은 앞 단어가 복구 전이라 세지 않고, "다" 앞 간격만 센다
    assert [i.t_ms for i in word_items(r) if "pause_count" in i.values] == [7000]


# ── 시각이 거꾸로이거나 겹친 단어 ───────────────────────────────────────────────


def test_a_word_with_reversed_times_is_skipped_but_passes_the_cursor():
    words = [word("가나", 1000, 1300), word("다라", 3000, 2500), word("마바", 4000, 4300)]
    r = run(5000, words)
    assert [i.t_ms for i in word_items(r)] == [1000, 4000]
    assert total(r.tally, "chars") == 4 and r.words_counted_until_ms == 4300
    # 간격은 빠진 단어를 건너뛰어 "가나" 끝에서 잰다 (2700)
    assert total(r.tally, "pause_ms") == 2700


def test_a_word_that_overlaps_the_previous_word_is_skipped_but_passes_the_cursor():
    words = [word("가나", 1000, 2000), word("다라", 1900, 2400), word("마바", 2500, 2800)]
    r = run(3000, words)
    assert [i.t_ms for i in word_items(r)] == [1000, 2500]
    assert r.words_counted_until_ms == 2800
    # 지난 요청에서 센 단어(커서)와 겹쳐도 같다
    again = run(3000, words[:2], words_since_ms=2000)
    assert word_items(again) == [] and again.words_counted_until_ms == 2400


def test_a_word_inside_a_longer_word_is_not_the_word_before_a_pause():
    words = [word("가나다라", 0, 5000), word("마", 1000, 2000), word("바사", 6000, 6500)]
    r = run(7000, words)
    # 간격은 겹친 "마" 를 건너뛰어 "가나다라" 끝에서 잰다 (1000, 멈춤 아님)
    assert total(r.tally, "pause_count") == 0 and total(r.tally, "pause_ms") == 0


def test_splitting_requests_does_not_change_pauses_around_an_overlapped_word():
    words = [word("가나", 0, 1000), word("다라", 500, 2500), word("마바", 4200, 4800)]
    whole = run(5000, words)
    first = run(3000, words[:2])
    later = run(
        5000,
        words,
        since_ms=first.counted_until_ms,
        words_since_ms=first.words_counted_until_ms,
    )
    # 둘 다 겹친 "다라" 를 건너뛰어 "가나" 끝에서 잰다 (3200)
    for r in (whole, later):
        assert total(r.tally, "pause_count") == 1 and total(r.tally, "pause_ms") == 3200


# ── 시간 몫 ────────────────────────────────────────────────────────────────────


def test_missed_requests_are_split_into_one_time_share_per_second():
    r = run(42_500, [], since_ms=40_000)
    assert r.tally == [
        TallyItem(t_ms=40_000, values={"ok_ms": 1000, "total_ms": 1000}),
        TallyItem(t_ms=41_000, values={"ok_ms": 1000, "total_ms": 1000}),
        TallyItem(t_ms=42_000, values={"ok_ms": 500, "total_ms": 500}),
    ]


def test_only_the_part_after_stt_recovery_is_ok_time():
    r = run(41_000, [], since_ms=40_000, stt_ok_since_ms=40_400)
    assert r.tally == [TallyItem(t_ms=40_000, values={"ok_ms": 600, "total_ms": 1000})]
