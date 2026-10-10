"""연구용 판정 대역 pace — 계약 모양, 커서, 그리고 지금 평가기와의 같음.

같음 검사는 대역이 지금 코치 평가기(`coach.evaluators.*`)를 옮긴 것임을 남긴다. 평가기를 지울 때
함께 지운다.
"""

from __future__ import annotations

from typing import Any

import pytest

from coach.vocab import Issue
from coach_lab.judges import filler, pace
from tests.lab.judge_helpers import (
    OldRun,
    check_shape,
    detection,
    merged,
    pace_words,
    plain,
    total,
)
from tests.unit.conftest import words as make_words


def marks(raw_words: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """군더더기 대역이 판정한 결과를 속도 모듈 입력 모양으로."""
    f = filler.judge({"words": pace_words(raw_words)}, 60_000)[0]
    return [
        {"start_ms": w["start_ms"], "end_ms": w["end_ms"], "is_filler": w["is_filler"]}
        for w in f.words or []
        if w["is_filler"]
    ]


def steady_words(t: int, cpm: float = 300) -> list[dict[str, Any]]:
    return pace_words(make_words(t, cpm=cpm, seconds=20))


def test_pace_result_shape_and_states():
    for cpm, state in ((200, "SLOW"), (300, "NORMAL"), (420, "FAST")):
        r = pace.judge({"words": steady_words(30_000, cpm), "fillers": []}, 30_000)[0]
        check_shape(r)
        assert r.state == state and r.measurable and r.area == "SPEED"
        assert bool(r.issues) == (state == "FAST")
    fast = pace.judge({"words": steady_words(30_000, 420)}, 30_000)[0]
    assert fast.issues[0].issue_type == Issue.PACE_FAST
    assert fast.issues[0].threshold == 350 and fast.issues[0].bad == 450
    assert fast == pace.judge({"words": steady_words(30_000, 420)}, 30_000)[0]


def test_pace_not_measurable_cases():
    few = pace.judge({"words": steady_words(30_000)[-3:]}, 30_000)[0]
    assert not few.measurable and few.state == "UNKNOWN" and few.metrics["cpm"] is None
    bad = pace.judge({"words": steady_words(30_000, 420), "stt_status": "degraded"}, 30_000)[0]
    assert not bad.measurable and bad.state == "UNKNOWN" and bad.metrics["cpm"] is None
    # 현재 평가기처럼 후보는 만들되 쓸 수 없게 둔다
    assert bad.issues and not bad.issues[0].actionable
    assert merged([bad]).get("ok_ms", 0) == 0


def test_pace_fillers_none_counts_nothing():
    words = steady_words(30_000)
    r = pace.judge(
        {"words": words, "fillers": None, "since_ms": 29_000, "words_since_ms": 20_000}, 30_000
    )[0]
    check_shape(r, since=29_000)
    assert not r.measurable and r.state == "UNKNOWN" and not r.issues
    assert r.words_counted_until_ms == 20_000
    assert total(r.tally, "chars") == 0 and total(r.tally, "total_ms") == 1000
    # 시간은 STT 상태대로 센다
    assert total(r.tally, "ok_ms") == 1000
    down = pace.judge({"words": words, "fillers": None, "stt_status": "degraded"}, 30_000)[0]
    assert total(down.tally, "ok_ms") == 0


def test_pace_tally_words_and_cursor():
    words = [plain("지난", 1000, 1300), plain("분기", 1500, 1900), plain("매출은", 2000, 2500)]
    r = pace.judge({"words": words, "since_ms": 0, "words_since_ms": -1}, 3_000)[0]
    check_shape(r)
    assert [i.t_ms for i in r.tally if "chars" in i.values] == [1000, 1500, 2000]
    assert total(r.tally, "chars") == 7 and total(r.tally, "speak_ms") == 1200
    assert r.words_counted_until_ms == 2500
    assert total(r.tally, "ok_ms") == 3000 and total(r.tally, "total_ms") == 3000
    # 커서를 넘기면 같은 단어를 다시 세지 않는다
    more = [*words, plain("늘었고", 3100, 3500)]
    again = pace.judge(
        {"words": more, "since_ms": 3_000, "words_since_ms": r.words_counted_until_ms}, 4_000
    )[0]
    assert total(again.tally, "chars") == 3 and again.words_counted_until_ms == 3500
    assert again.counted_until_ms == 4_000
    check_shape(again, since=3_000)


def test_pace_filler_chars_are_left_out_but_time_is_kept():
    words = [plain("지난", 1000, 1300), plain("음", 1400, 1700), plain("분기", 1800, 2200)]
    f = [{"start_ms": 1400, "end_ms": 1700, "is_filler": True}]
    r = pace.judge({"words": words, "fillers": f}, 3_000)[0]
    assert total(r.tally, "chars") == 4 and total(r.tally, "speak_ms") == 1000


def test_pace_pending_filler_stops_the_word_cursor():
    words = [plain("지난", 1000, 1300), plain("그", 1500, 1700), plain("분기", 1800, 2200)]
    f = [{"start_ms": 1500, "end_ms": 1700, "is_filler": None}]
    r = pace.judge({"words": words, "fillers": f}, 3_000)[0]
    assert total(r.tally, "chars") == 2 and r.words_counted_until_ms == 1300
    # 판정이 정해지면 보류 단어부터 센다
    f = [{"start_ms": 1500, "end_ms": 1700, "is_filler": False}]
    r2 = pace.judge(
        {"words": words, "fillers": f, "words_since_ms": 1300, "since_ms": 3000}, 4_000
    )[0]
    assert total(r2.tally, "chars") == 3 and r2.words_counted_until_ms == 2200


def test_pace_ignored_pending_word_does_not_block_later_words():
    words = [plain("그", 500, 800), plain("지난", 1500, 1800), plain("분기", 2000, 2400)]
    f = [{"start_ms": 500, "end_ms": 800, "is_filler": None}]
    # 복구 전(start < stt_ok_since_ms)이라 무시하는 단어의 보류는 뒤 단어를 막지 않는다
    r = pace.judge({"words": words, "fillers": f, "stt_ok_since_ms": 1000}, 3_000)[0]
    assert total(r.tally, "chars") == 4 and r.words_counted_until_ms == 2400
    # STT 가 ok 가 아닐 때도 같다
    r = pace.judge({"words": words, "fillers": f, "stt_status": "degraded"}, 3_000)[0]
    assert total(r.tally, "chars") == 0 and r.words_counted_until_ms == 2400


def test_pace_pause_counting():
    words = [
        plain("가", 1000, 1300),
        plain("나", 3000, 3300),  # 간격 1700 — 멈춤 아님
        plain("다", 6000, 6300),  # 간격 2700 — 멈춤
        plain("라", 8300, 8600),  # 간격 2000 — 기준 초과가 아니라 멈춤 아님
    ]
    r = pace.judge({"words": words}, 9_000)[0]
    assert total(r.tally, "pause_count") == 1 and total(r.tally, "pause_ms") == 2700
    pause_item = next(i for i in r.tally if "pause_count" in i.values)
    assert pause_item.t_ms == 6000  # 뒤 단어의 몫
    # 앞 단어가 이미 센 단어여도(커서 앞) 간격은 뒤 단어가 센다
    r2 = pace.judge({"words": words, "words_since_ms": 3300, "since_ms": 9000}, 10_000)[0]
    assert total(r2.tally, "pause_count") == 1


def test_pace_words_before_stt_recovery_are_skipped():
    words = [plain("가", 1000, 1300), plain("나", 4000, 4300), plain("다", 7000, 7300)]
    r = pace.judge({"words": words, "stt_ok_since_ms": 3500}, 8_000)[0]
    assert total(r.tally, "chars") == 2  # '가' 는 건너뛴다
    assert r.words_counted_until_ms == 7300  # 커서는 넘긴다
    # 간격: '나' 의 앞 단어가 복구 전이라 멈춤으로 세지 않는다 (3000 > 2000 이지만)
    assert total(r.tally, "pause_count") == 1  # '다' 의 몫만
    assert total(r.tally, "ok_ms") == 4500 and total(r.tally, "total_ms") == 8000


def test_pace_summarize_and_criteria():
    t = {
        "SPEED": {
            "chars": 482,
            "speak_ms": 90800,
            "pause_count": 3,
            "ok_ms": 176000,
            "total_ms": 182000,
        }
    }
    s = pace.summarize(t)["SPEED"]
    assert s["cpm"] == 318.5 and s["pause_count"] == 3
    assert s["measured_ratio"] == pytest.approx(0.967, abs=1e-3)
    empty = pace.summarize({"SPEED": {"total_ms": 4000}})["SPEED"]
    assert empty["cpm"] is None and empty["measured_ratio"] == 0.0
    c = pace.criteria()["PACE_FAST"]
    assert (c.metric, c.threshold, c.bad, c.onset_lag_ms, c.offset_lag_ms) == (
        "cpm",
        350,
        450,
        7500,
        7500,
    )


@pytest.mark.parametrize(
    ("cpm", "filler_every", "recovery"),
    [(300, 0, None), (420, 0, None), (400, 4, None), (440, 0, 24_000), (380, 5, 28_000)],
)
def test_pace_equals_the_current_evaluator(cpm, filler_every, recovery):
    """CPM · 짧은 창 CPM · 말한 시간 · 심각도 · 신뢰도가 지금 평가기와 같다 (군더더기는 뺀다)."""
    for t in (20_000, 25_000, 30_000):
        raw = make_words(t, cpm=cpm, seconds=15, filler_every=filler_every)
        old = OldRun()
        old.state.stt_ok_since_ms = recovery
        tick = old.step(t, speech={"stt_status": "ok", "words": raw})
        new = pace.judge(
            {"words": pace_words(raw), "fillers": marks(raw), "stt_ok_since_ms": recovery}, t
        )[0]
        assert new.metrics["cpm"] == tick.metrics["cpm"], t
        assert new.metrics["cpm_short"] == tick.metrics["cpm_short"], t
        d = detection(tick, Issue.PACE_FAST)
        assert bool(new.issues) == (d is not None), t
        if d is not None:
            assert new.issues[0].severity == pytest.approx(d.severity)
            assert new.issues[0].confidence == pytest.approx(d.confidence)
            assert new.issues[0].evidence["speak_ms"] == d.evidence["speak_ms"]
