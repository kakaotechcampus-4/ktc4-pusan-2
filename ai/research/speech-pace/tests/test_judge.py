"""판정 규칙 — 상태 · 문제 · 심각도 · 신뢰도 · 창 · 잴 수 없음 (pace.md 5-3 · 5-4 · 6-1 · 7절)."""

from __future__ import annotations

import pytest

from pace.config import PaceConfig
from pace.schemas import TallyItem
from tests.helpers import CFG, run, steady, total, word, word_items

T = 60_000


# ── 상태와 문제 ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("chars", "dur_ms", "state"),
    [(4, 1000, "SLOW"), (3, 600, "NORMAL"), (3, 450, "FAST")],
)
def test_steady_speech_is_slow_normal_or_fast_by_the_recent_window_cpm(chars, dur_ms, state):
    r = run(T, steady(T, chars=chars, dur_ms=dur_ms))
    assert r.measurable and r.state == state
    assert r.metrics.cpm == round(chars * 60_000 / dur_ms, 1)


@pytest.mark.parametrize(("chars", "dur_ms", "cpm"), [(7, 1200, 350.0), (11, 2400, 275.0)])
def test_cpm_equal_to_a_threshold_is_normal(chars, dur_ms, cpm):
    r = run(T, steady(T, chars=chars, dur_ms=dur_ms))
    assert r.metrics.cpm == cpm
    assert r.state == "NORMAL" and r.issues == []


@pytest.mark.parametrize(("chars", "dur_ms", "state"), [(7, 1190, "FAST"), (11, 2410, "SLOW")])
def test_cpm_just_past_a_threshold_is_fast_or_slow(chars, dur_ms, state):
    assert run(T, steady(T, chars=chars, dur_ms=dur_ms)).state == state


@pytest.mark.parametrize(
    ("chars", "dur_ms", "severity"),
    [
        (3, 450, 0.75),  # 400: 빠름 기준 350 과 아주 빠름 450 의 가운데
        (3, 360, 1.0),  # 500: 아주 빠름보다 빨라도 1.0 에서 자른다
        (4, 1000, 0.7333),  # 240: 느림 기준 275 에서 아주 느림 200 쪽으로 35/75
        (5, 2000, 1.0),  # 150
    ],
)
def test_severity_is_half_at_the_threshold_and_full_at_the_bad_value(chars, dur_ms, severity):
    [issue] = run(T, steady(T, chars=chars, dur_ms=dur_ms)).issues
    assert issue.severity == severity


def test_issues_carry_their_threshold_bad_value_and_evidence():
    fast = run(T, steady(T, chars=3, dur_ms=450)).issues[0]
    assert (fast.threshold, fast.bad) == (350.0, 450.0)
    assert fast.evidence == {"cpm": 400.0, "window_ms": 15_000}
    assert fast.actionable and fast.persistence_sec == 0.0
    slow = run(T, steady(T, chars=4, dur_ms=1000)).issues[0]
    assert (slow.threshold, slow.bad) == (275.0, 200.0)
    # 느림도 actionable 이다. 말하지 않는 것은 코치가 정한다
    assert slow.actionable


def test_confidence_grows_with_speaking_time_in_the_recent_window():
    burst = run(T, steady(T, chars=3, dur_ms=450, seconds=4.5))
    assert burst.metrics.speak_ms == 4_500
    assert burst.issues[0].confidence == pytest.approx(0.6 + 0.4 * 4_500 / 8_000)
    full = run(T, steady(T, chars=3, dur_ms=450))
    assert full.metrics.speak_ms >= 8_000 and full.issues[0].confidence == 1.0


# ── 창 ─────────────────────────────────────────────────────────────────────────


def test_only_words_inside_the_recent_window_make_the_cpm():
    old = steady(T - 20_000, chars=3, dur_ms=360, seconds=20)  # 아주 빠른 말, 창 밖
    recent = steady(T, chars=3, dur_ms=600)
    r = run(T, old + recent)
    assert r.metrics.cpm == 300.0 and r.metrics.speak_ms == 15_000


def test_too_little_speech_in_the_recent_window_is_unknown_but_new_words_still_count():
    r = run(T, steady(T, chars=3, dur_ms=600, seconds=2.4), since_ms=T - 1000)  # 4 단어 2.4초
    assert r.metrics.cpm is None and r.metrics.speak_ms == 2_400
    assert not r.measurable and r.state == "UNKNOWN"
    # 시간 판정이 쓰는 글자 수는 잃지 않는다
    assert total(r.tally, "chars") == 12 and r.words_counted_until_ms == T


def test_too_few_words_is_unknown_even_with_enough_speaking_time():
    r = run(T, steady(T, chars=6, dur_ms=1200, seconds=4.8))  # 4 단어 4.8초
    assert r.metrics.speak_ms == 4_800
    assert r.metrics.cpm is None and r.state == "UNKNOWN"


def test_short_window_cpm_can_exist_while_the_recent_window_is_not_measurable():
    r = run(T, steady(T, chars=3, dur_ms=700, seconds=2.8))  # 4 단어 2.8초
    assert not r.measurable and r.metrics.cpm is None
    assert r.metrics.cpm_short == 257.1


def test_filler_and_pending_words_are_left_out_of_the_window_cpm():
    words = []
    t = T - 15_000
    while t + 1000 <= T:
        words += [word("발표를", t, t + 600), word("음", t + 600, t + 1000)]
        t += 1000
    marks = [{**w, "is_filler": True} for w in words if w["word"] == "음"]
    # 군더더기를 모르면 "음" 이 글자 수 · 발화 시간에 들어가 느려 보인다
    assert run(T, words).metrics.cpm == 240.0
    marked = run(T, words, fillers=marks)
    assert marked.metrics.cpm == 300.0 and marked.metrics.speak_ms == 9_000
    pending = run(T, words, fillers=[{**m, "is_filler": None} for m in marks])
    assert pending.metrics.cpm == 300.0


def test_after_stt_recovers_the_window_starts_at_the_recovery():
    ok_since = T - 8_000
    before = steady(ok_since - 500, chars=4, dur_ms=1000, seconds=10)  # 느린 말, 복구 전
    after = steady(T, chars=3, dur_ms=450, seconds=8)
    r = run(T, before + after, stt_ok_since_ms=ok_since)
    assert r.metrics.cpm == 400.0 and r.state == "FAST"
    assert r.issues[0].evidence["window_ms"] == 8_000
    # 복구 전 단어는 세지 않고 커서만 넘긴다
    assert total(r.tally, "chars") == 3 * len(after) and r.words_counted_until_ms == T


# ── 잴 수 없음 (7절) ───────────────────────────────────────────────────────────


@pytest.mark.parametrize("status", ["connecting", "reconnecting", "degraded", "closed"])
def test_stt_not_ok_is_unknown_and_passes_the_word_cursor_without_counting(status):
    r = run(T, steady(T, chars=3, dur_ms=450), stt_status=status, since_ms=T - 1000)
    assert r.state == "UNKNOWN" and not r.measurable and r.issues == []
    assert (r.metrics.cpm, r.metrics.cpm_short, r.metrics.speak_ms) == (None, None, None)
    assert word_items(r) == [] and r.words_counted_until_ms == T
    assert r.tally == [TallyItem(t_ms=T - 1000, values={"total_ms": 1000})]


def test_failed_filler_judgment_counts_no_words_and_keeps_the_word_cursor():
    r = run(
        T,
        steady(T, chars=3, dur_ms=450),
        fillers=None,
        since_ms=T - 1000,
        words_since_ms=T - 5_000,
    )
    assert r.state == "UNKNOWN" and not r.measurable and r.issues == []
    assert (r.metrics.cpm, r.metrics.cpm_short, r.metrics.speak_ms) == (None, None, None)
    assert r.words_counted_until_ms == T - 5_000
    # 단어를 세지 못한 시간이라 ok_ms 에 넣지 않는다 (STT 가 ok 여도)
    assert r.tally == [TallyItem(t_ms=T - 1000, values={"total_ms": 1000})]


def test_no_words_is_unknown_with_only_the_time_share():
    r = run(T, [], since_ms=T - 1000)
    assert r.state == "UNKNOWN" and r.metrics.cpm is None
    assert r.tally == [TallyItem(t_ms=T - 1000, values={"ok_ms": 1000, "total_ms": 1000})]


# ── 같은 입력 같은 결과 · 기준값 ───────────────────────────────────────────────


def test_same_inputs_give_the_same_result_whatever_the_word_order():
    words = steady(T, chars=3, dur_ms=450)
    assert run(T, words) == run(T, words) == run(T, words[::-1])


def test_thresholds_and_criteria_version_come_from_the_config():
    words = steady(T, chars=3, dur_ms=600)  # 300
    strict = PaceConfig(**{**CFG.model_dump(), "fast_cpm": 280.0, "fast_cpm_bad": 380.0})
    r = run(T, words, config=strict)
    assert r.state == "FAST" and r.issues[0].threshold == 280.0
    assert r.criteria_version != run(T, words).criteria_version
