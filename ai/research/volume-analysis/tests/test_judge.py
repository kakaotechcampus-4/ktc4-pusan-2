"""judge 판정 규칙 — 작음 · 잴 수 없음 · 에너지 Leq · 침묵 자르기 · 긴 침묵 세기 · 커서 (volume.md 10절)."""

from __future__ import annotations

import math
from typing import Any

import pytest

from volume.config import Config
from volume.core import judge

BASE = -29.5


def rec(
    t_ms: int,
    level_db: float | None = -30.0,
    voiced_ms: int = 800,
    silence_ms: int = 0,
    audio_live: bool = True,
    duration_ms: int = 1000,
) -> dict[str, Any]:
    return {
        "t_ms": t_ms,
        "duration_ms": duration_ms,
        "level_db": level_db,
        "voiced_ms": voiced_ms,
        "silence_ms": silence_ms,
        "audio_live": audio_live,
    }


def run(records: list[dict[str, Any]], t_ms: int, since_ms: int | None = None, base=BASE):
    inputs = {"records": records, "base_level_db": base, "since_ms": t_ms - 1000 if since_ms is None else since_ms}
    return judge(inputs, t_ms)


def speech(start: int, end: int, level_db: float = -30.0) -> list[dict[str, Any]]:
    return [rec(t, level_db) for t in range(start, end, 1000)]


# --------------------------------------------------------------------------
# VOLUME
# --------------------------------------------------------------------------


def test_results_are_volume_then_pause() -> None:
    volume, pause = run(speech(0, 5000), 5000)
    assert (volume.area, pause.area) == ("VOLUME", "PAUSE")
    assert volume.evaluator == pause.evaluator == "volume"
    assert volume.criteria_version == pause.criteria_version


def test_same_as_baseline_is_normal() -> None:
    volume, _ = run(speech(0, 5000, BASE), 5000)
    assert volume.measurable
    assert volume.state == "NORMAL"
    assert volume.metrics.voice_diff_db == pytest.approx(0.0)
    assert volume.issues == []


def test_quiet_is_low() -> None:
    volume, _ = run(speech(0, 5000, BASE - 10), 5000)
    assert volume.state == "LOW"
    assert volume.metrics.voice_diff_db == pytest.approx(-10.0)
    [issue] = volume.issues
    assert issue.issue_type == "VOLUME_LOW"
    assert issue.area == "VOLUME"
    assert 0.5 < issue.severity < 1.0
    assert issue.confidence == 1.0


def test_severity_caps_at_one() -> None:
    volume, _ = run(speech(0, 5000, BASE - 40), 5000)
    assert volume.issues[0].severity == 1.0


def test_no_baseline_is_unknown() -> None:
    volume, pause = run(speech(0, 5000), 5000, base=None)
    assert not volume.measurable
    assert volume.state == "UNKNOWN"
    assert volume.metrics.voice_diff_db is None
    assert volume.issues == []
    # 침묵은 기준 음량과 관계없이 잰다
    assert pause.measurable


def test_audio_dead_is_unknown_for_both() -> None:
    records = speech(0, 4000, BASE - 10) + [rec(4000, None, 0, 0, audio_live=False)]
    volume, pause = run(records, 5000)
    assert (volume.state, pause.state) == ("UNKNOWN", "UNKNOWN")
    assert not volume.measurable and not pause.measurable
    assert volume.issues == [] and pause.issues == []
    assert pause.metrics.silence_ms is None


def test_too_few_spoken_seconds_is_unknown() -> None:
    cfg = Config()
    records = speech(0, (cfg.min_voiced_seconds - 1) * 1000, BASE - 10)
    volume, _ = run(records, len(records) * 1000)
    assert volume.state == "UNKNOWN"


def test_short_voicing_is_not_a_spoken_second() -> None:
    # voiced_ms 가 기준 미만인 1초는 평활 창에서 빠진다
    cfg = Config()
    records = speech(0, 3000, BASE) + [rec(3000, BASE - 30, voiced_ms=cfg.voiced_min_ms - 1)]
    volume, _ = run(records, 4000)
    assert volume.metrics.level_db == pytest.approx(BASE)


def test_level_is_energy_mean_not_db_mean() -> None:
    records = [rec(0, -20.0), rec(1000, -40.0), rec(2000, -40.0)]
    volume, _ = run(records, 3000)
    expected = 10 * math.log10((10**-2 + 2 * 10**-4) / 3)
    assert volume.metrics.level_db == pytest.approx(expected, abs=0.01)
    assert volume.metrics.level_db != pytest.approx((-20 - 40 - 40) / 3, abs=0.5)


def test_smoothing_window_drops_old_seconds() -> None:
    cfg = Config()
    old = speech(0, 10000, BASE - 20)
    recent = speech(10000, 10000 + cfg.smoothing_ms, BASE)
    volume, _ = run(old + recent, 10000 + cfg.smoothing_ms)
    assert volume.state == "NORMAL"


def test_low_persistence_counts_back_from_last_second() -> None:
    # 평소 10초 뒤 작게 6초. 짧은 창 대부분이 작아진 뒤부터 이어진 시간만 센다
    records = speech(0, 10000, BASE) + speech(10000, 16000, BASE - 12)
    volume, _ = run(records, 16000)
    [issue] = volume.issues
    assert 0 < issue.persistence_sec <= 6


# --------------------------------------------------------------------------
# PAUSE
# --------------------------------------------------------------------------


def silent(start: int, end: int, before_ms: int = 0) -> list[dict[str, Any]]:
    """start 부터 이어지는 무음 (silence_ms 는 1초 끝까지 누적)."""
    return [rec(t, None, 0, before_ms + t - start + 1000) for t in range(start, end, 1000)]


def test_long_silence_is_silent() -> None:
    records = speech(0, 3000) + silent(3000, 10000)
    _, pause = run(records, 10000)
    assert pause.state == "SILENT"
    assert pause.metrics.silence_ms == 7000
    [issue] = pause.issues
    assert issue.issue_type == "LONG_SILENCE"
    assert issue.persistence_sec == pytest.approx(7.0)


def test_short_silence_is_normal() -> None:
    records = speech(0, 3000) + silent(3000, 6000)
    _, pause = run(records, 6000)
    assert pause.state == "NORMAL"
    assert pause.issues == []


def test_silence_is_trimmed_after_audio_returns() -> None:
    # 35000 에 끊겼다가 36000 부터 다시 흐른다. FE 가 끊김 전부터 이어 센 큰 값을 보내도
    # 다시 흐른 첫 1초의 시작(36000)부터만 침묵이다
    records = [rec(t, None, 0, t - 30000 + 1000) for t in range(30000, 41000, 1000)]
    records[5]["audio_live"] = False
    _, pause = run(records, 41000)
    assert pause.metrics.silence_ms == 41000 - 36000


def test_silence_is_not_trimmed_without_gap_in_window() -> None:
    records = [rec(t, None, 0, 20000 + t - 30000 + 1000) for t in range(30000, 41000, 1000)]
    _, pause = run(records, 41000)
    assert pause.metrics.silence_ms == records[-1]["silence_ms"]


def test_long_silence_counted_once() -> None:
    cfg = Config()
    records = speech(0, 2000) + silent(2000, 20000)
    counts = []
    for end in range(3000, 20001, 1000):
        window = [r for r in records if r["t_ms"] + r["duration_ms"] <= end]
        _, pause = run(window, end)
        counts += [item.values.long_silence_count for item in pause.tally]
    assert sum(counts) == 1
    # 기준을 처음 넘은 1초에서 센다
    crossed = 2000 + cfg.long_silence_ms
    assert counts[(crossed - 2000) // 1000] == 1


# --------------------------------------------------------------------------
# 기록 정리 · tally · 커서
# --------------------------------------------------------------------------


def test_duplicate_second_keeps_first() -> None:
    records = speech(0, 4000, BASE) + [rec(4000, BASE), rec(4000, BASE - 30)]
    volume, _ = run(records, 5000)
    assert volume.metrics.level_db == pytest.approx(BASE)
    assert len(volume.tally) == 1


def test_records_after_judgment_time_are_ignored() -> None:
    records = speech(0, 5000, BASE) + [rec(5000, BASE - 30)]
    volume, _ = run(records, 5000)
    assert volume.metrics.level_db == pytest.approx(BASE)


def test_last_partial_piece() -> None:
    records = speech(0, 4000, BASE) + [rec(4000, BASE, voiced_ms=400, duration_ms=600)]
    volume, pause = run(records, 4600, since_ms=4000)
    [item] = volume.tally
    assert item.values.total_ms == 600
    assert item.values.audio_live_ms == 600
    assert pause.tally[0].values.total_ms == 600


def test_tally_values() -> None:
    volume, pause = run([rec(40000, -31.2, 820)], 41000)
    v = volume.tally[0].values
    assert volume.tally[0].t_ms == 40000
    assert v.voiced_ms == 820
    assert v.voiced_energy == pytest.approx(10 ** (-31.2 / 10) * 820)
    assert v.level_voiced_ms == 820
    assert v.base_db_ms == pytest.approx(BASE * 820)
    assert (v.audio_live_ms, v.total_ms) == (1000, 1000)
    assert pause.tally[0].values.total_ms == 1000


def test_tally_without_baseline_has_no_level_voiced() -> None:
    volume, _ = run([rec(40000)], 41000, base=None)
    v = volume.tally[0].values
    assert v.level_voiced_ms == 0 and v.base_db_ms == 0


def test_missing_seconds_count_only_total() -> None:
    volume, pause = run([rec(40000)], 41000, since_ms=38000)
    assert [(i.t_ms, i.values.total_ms) for i in volume.tally] == [(38000, 2000), (40000, 1000)]
    gap = volume.tally[0].values
    assert gap.voiced_ms == gap.audio_live_ms == 0
    assert pause.tally[0].values.audio_live_ms == 0


def test_tally_only_new_seconds() -> None:
    volume, _ = run(speech(0, 10000), 10000, since_ms=8000)
    assert [i.t_ms for i in volume.tally] == [8000, 9000]


def test_recall_with_cursor_has_empty_tally() -> None:
    records = speech(0, 10000)
    first, _ = run(records, 10000, since_ms=9000)
    again, _ = run(records, 10000, since_ms=first.counted_until_ms)
    assert again.tally == []


def test_cursor_never_goes_back() -> None:
    volume, pause = run(speech(0, 10000), 8000, since_ms=9000)
    assert volume.counted_until_ms == pause.counted_until_ms == 9000
    assert volume.tally == []


def test_one_by_one_equals_all_at_once() -> None:
    # 요청이 빠져 여러 1초를 한 번에 세도 1초씩 센 것과 같다
    records = speech(0, 8000, BASE) + speech(8000, 14000, BASE - 12) + silent(14000, 22000)
    step = []
    for end in range(1000, 22001, 1000):
        volume, pause = run([r for r in records if r["t_ms"] < end], end, since_ms=end - 1000)
        step += [(i.t_ms, i.values) for i in volume.tally + pause.tally]
    volume, pause = run(records, 22000, since_ms=0)
    once = [(i.t_ms, i.values) for i in volume.tally + pause.tally]
    assert sorted(step, key=lambda x: (x[0], type(x[1]).__name__)) == sorted(
        once, key=lambda x: (x[0], type(x[1]).__name__)
    )


def test_deterministic() -> None:
    records = speech(0, 8000, BASE - 8) + silent(8000, 15000)
    assert run(records, 15000) == run(records, 15000)


def test_unknown_fields_are_ignored() -> None:
    record = {**rec(40000), "extra": 1}
    volume, _ = judge({"records": [record], "base_level_db": BASE, "since_ms": 40000, "x": 1}, 41000)
    assert volume.tally[0].values.voiced_ms == 800
