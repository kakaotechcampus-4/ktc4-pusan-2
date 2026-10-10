"""연구용 판정 대역 filler — 계약 모양, 커서, 그리고 지금 평가기와의 같음.

같음 검사는 대역이 지금 코치 평가기(`coach.evaluators.*`)를 옮긴 것임을 남긴다. 평가기를 지울 때
함께 지운다.
"""

from __future__ import annotations

import pytest

from coach.vocab import Issue
from coach_lab.judges import filler
from tests.lab.judge_helpers import OldRun, check_shape, detection, merged, pace_words, plain


def filler_words(n: int, *, start: int = 1000, step: int = 1500, word: str = "음"):
    return [plain(word, start + i * step, start + i * step + 300) for i in range(n)]


def test_filler_result_shape_and_words():
    words = [plain("지난", 1000, 1300), plain("음", 1500, 1800), plain("어어.", 2000, 2300)]
    r = filler.judge({"words": words}, 3_000)[0]
    check_shape(r)
    assert r.words == [
        {
            "start_ms": 1000,
            "end_ms": 1300,
            "word": "지난",
            "tier": None,
            "is_filler": False,
            "reason": "사전에 없음",
        },
        {
            "start_ms": 1500,
            "end_ms": 1800,
            "word": "음",
            "tier": "T1",
            "is_filler": True,
            "reason": "채움말",
        },
        {
            "start_ms": 2000,
            "end_ms": 2300,
            "word": "어어.",
            "tier": "T1",
            "is_filler": True,
            "reason": "채움말",
        },
    ]
    assert r.metrics["recent_filler_count"] == 2 and r.state == "NORMAL" and r.measurable
    assert r.metrics["filler_per_min"] is None  # 10초 전에는 분당 수를 내지 않는다
    assert r == filler.judge({"words": words}, 3_000)[0]


def test_filler_frequent_issue_and_state():
    r = filler.judge({"words": filler_words(5)}, 20_000)[0]
    assert r.state == "NORMAL" and not r.issues
    r = filler.judge({"words": filler_words(6)}, 20_000)[0]
    assert r.state == "HIGH" and r.issues[0].issue_type == Issue.FILLER_FREQUENT
    assert r.issues[0].severity == 0.5 and r.metrics["filler_per_min"] == 18.0
    r = filler.judge({"words": filler_words(15, step=1000)}, 20_000)[0]
    assert r.issues[0].severity == 1.0
    assert (r.issues[0].threshold, r.issues[0].bad) == (6, 15)


def test_filler_not_ok_is_unknown():
    r = filler.judge({"words": filler_words(8), "stt_status": "degraded"}, 20_000)[0]
    assert not r.measurable and r.state == "UNKNOWN" and not r.issues
    assert r.metrics["recent_filler_count"] is None and r.metrics["filler_per_min"] is None


def test_filler_words_before_recovery_are_ignored():
    r = filler.judge({"words": filler_words(8), "stt_ok_since_ms": 6000}, 20_000)[0]
    assert all(w["start_ms"] >= 6000 for w in r.words or [])
    assert r.metrics["recent_filler_count"] == 4


def test_filler_tally_and_cursor():
    words = [plain("지난", 1000, 1300), plain("음", 1500, 1800), plain("음", 2500, 2800)]
    r = filler.judge({"words": words, "since_ms": 0}, 3_000)[0]
    assert r.tally[0].t_ms == 1500
    assert r.tally[0].values == {"filler_count": 1, "filler_t1": 1, "filler_word:음": 1}
    assert merged([r]) == {
        "filler_count": 2,
        "filler_t1": 2,
        "filler_word:음": 2,
        "elapsed_ms": 3000,
        "total_ms": 3000,
    }
    assert r.words_counted_until_ms == 2800
    again = filler.judge({"words": words, "since_ms": 3000, "words_since_ms": 2800}, 4_000)[0]
    assert merged([again]) == {"elapsed_ms": 1000, "total_ms": 1000}
    assert again.words_counted_until_ms == 2800
    check_shape(again, since=3000)


def test_filler_summarize_and_criteria():
    t = {
        "FILLER": {
            "filler_count": 7,
            "filler_t1": 4,
            "filler_t3": 3,
            "filler_word:음": 4,
            "filler_word:그니까": 3,
            "elapsed_ms": 175_000,
            "total_ms": 180_000,
        }
    }
    s = filler.summarize(t)["FILLER"]
    assert s["filler_count"] == 7 and s["filler_per_min"] == 2.4
    assert s["by_tier"] == {"T1": 4, "T2": 0, "T3": 3}
    assert s["by_word"] == {"음": 4, "그니까": 3}
    assert s["measured_ratio"] == pytest.approx(0.9722, abs=1e-4)
    assert filler.summarize({})["FILLER"]["filler_per_min"] is None
    c = filler.criteria()["FILLER_FREQUENT"]
    assert (c.metric, c.threshold, c.bad, c.onset_lag_ms, c.offset_lag_ms) == (
        "recent_filler_count",
        6,
        15,
        30000,
        30000,
    )


def test_filler_equals_the_current_evaluator_in_the_first_minute():
    """한 판 안에서(60초 창이 Take 시작에 걸쳐 있을 때) 개수 · 분당 수 · 심각도가 같다."""
    words = []
    for i in range(1, 57):  # 1초 간격, 3개 중 하나는 군더더기
        word = ["음", "어어", "으음."][i % 3] if i % 3 != 0 else "지난"
        words.append(
            {"w": word, "start_ms": i * 1000 - 600, "end_ms": i * 1000 - 200, "final": True}
        )
    old = OldRun()
    for t in range(1000, 60_001, 1000):
        have = [w for w in words if w["end_ms"] <= t]
        tick = old.step(t, speech={"stt_status": "ok", "words": have})
        new = filler.judge({"words": pace_words(have)}, t)[0]
        assert new.metrics["recent_filler_count"] == tick.metrics["recent_filler_count"], t
        assert new.metrics["filler_count_30s"] == tick.metrics["filler_count_30s"], t
        assert new.metrics["filler_per_min"] == tick.metrics["filler_per_min"], t
        d = detection(tick, Issue.FILLER_FREQUENT)
        assert bool(new.issues) == (d is not None), t
        if d is not None:
            assert new.issues[0].severity == pytest.approx(d.severity)
