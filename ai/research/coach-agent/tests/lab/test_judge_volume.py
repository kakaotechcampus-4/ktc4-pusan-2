"""연구용 판정 대역 volume — 계약 모양과 커서."""

from __future__ import annotations

from typing import Any

import pytest

from coach.vocab import Issue
from coach_lab.judges import volume
from tests.lab.judge_helpers import check_shape, total


def vrec(i: int, level: float | None = -30.0, **kw: Any) -> dict[str, Any]:
    voiced_ms = 900 if level is not None else 0
    base = {
        "t_ms": i * 1000,
        "duration_ms": 1000,
        "level_db": level,
        "voiced_ms": voiced_ms,
        "silence_ms": 0,
        "audio_live": True,
    }
    return {**base, **kw}


def vjudge(records, t, base=-30.0, since=0):
    vol, pause = volume.judge({"records": records, "base_level_db": base, "since_ms": since}, t)
    return vol, pause


def test_volume_result_shape_and_states():
    recs = [vrec(i, -30.0) for i in range(10)]
    vol, pause = vjudge(recs, 10_000)
    check_shape(vol)
    check_shape(pause)
    assert (vol.area, pause.area) == ("VOLUME", "PAUSE")
    assert vol.evaluator == pause.evaluator == "volume"
    assert vol.state == "NORMAL" and vol.measurable and vol.metrics["voice_diff_db"] == 0.0
    assert pause.state == "NORMAL" and pause.measurable and pause.metrics["silence_ms"] == 0
    assert (vol, pause) == vjudge(recs, 10_000)
    quiet = [vrec(i, -40.0) for i in range(10)]
    vol, _ = vjudge(quiet, 10_000)
    assert vol.state == "LOW" and vol.issues[0].issue_type == Issue.VOLUME_LOW
    assert vol.issues[0].severity == pytest.approx(0.5 + 0.5 * (10 - 6) / 9)


def test_volume_unknown_cases():
    recs = [vrec(i, -40.0) for i in range(10)]
    vol, _ = vjudge(recs, 10_000, base=None)
    assert not vol.measurable and vol.state == "UNKNOWN" and not vol.issues
    assert vol.metrics["level_db"] == -40.0 and vol.metrics["voice_diff_db"] is None
    few = [vrec(i, None) for i in range(8)] + [vrec(8, -40.0), vrec(9, -40.0)]
    vol, _ = vjudge(few, 10_000)
    assert not vol.measurable and vol.state == "UNKNOWN"  # 말한 1초가 3개 미만
    dead = [*recs[:9], vrec(9, None, audio_live=False, silence_ms=60_000)]
    vol, pause = vjudge(dead, 10_000)
    assert not vol.measurable and not pause.measurable and pause.state == "UNKNOWN"
    assert pause.metrics["silence_ms"] is None
    # 오디오가 죽어 있으면 이슈를 내지 않는다
    assert not vol.issues and not pause.issues
    dead_quiet = [vrec(i, -40.0, audio_live=False) for i in range(10)]
    vol, pause = vjudge(dead_quiet, 10_000)
    assert not vol.issues and not pause.issues


def test_volume_level_db_is_kept_when_the_last_second_has_no_audio():
    recs = [vrec(i, -40.0) for i in range(9)] + [vrec(9, None, audio_live=False)]
    vol, _ = vjudge(recs, 10_000)
    assert vol.metrics["level_db"] == -40.0 and vol.metrics["voice_diff_db"] is None
    assert not vol.measurable


def test_volume_smoothing_uses_the_last_five_seconds():
    recs = [vrec(i, -45.0) for i in range(5)] + [vrec(i, -30.0) for i in range(5, 10)]
    vol, _ = vjudge(recs, 10_000)
    assert vol.metrics["voice_diff_db"] == 0.0  # 앞 5초는 평균에 들지 않는다
    vol, _ = vjudge(recs, 8_000)
    assert vol.metrics["voice_diff_db"] == -6.0  # 3~7초 다섯 개: 앞의 둘이 -15, 뒤의 셋이 0


def test_volume_silence_trim_and_long_silence_issue():
    recs = [vrec(i, -30.0) for i in range(10)] + [
        vrec(10 + k, None, silence_ms=(k + 1) * 1000) for k in range(8)
    ]
    _, pause = vjudge(recs, 18_000)
    assert pause.metrics["silence_ms"] == 8000 and pause.state == "SILENT"
    iss = pause.issues[0]
    assert iss.issue_type == Issue.LONG_SILENCE and iss.actionable
    assert iss.severity == pytest.approx(0.5 + 0.5 * 3 / 10)
    assert (iss.threshold, iss.bad) == (5000, 15000)
    # 오디오가 끊겼다 돌아오면 돌아온 뒤의 침묵만 센다
    revived = [vrec(i, -30.0) for i in range(5)]
    revived += [vrec(i, None, audio_live=False, silence_ms=60_000) for i in range(5, 8)]
    revived += [vrec(i, None, silence_ms=60_000) for i in range(8, 12)]
    _, pause = vjudge(revived, 12_000)
    assert pause.metrics["silence_ms"] == 3000  # 8초에 돌아온 첫 1초가 끝난 9초부터 센다
    assert pause.state == "NORMAL"


def test_volume_tally_values_and_base_db_ms():
    recs = [vrec(0, -30.0), vrec(1, -40.0, voiced_ms=500), vrec(2, None, silence_ms=1000)]
    vol, pause = vjudge(recs, 3_000, base=-30.0)
    v0, v1, v2 = vol.tally
    assert v0.values["voiced_ms"] == 900 and v0.values["level_voiced_ms"] == 900
    assert v0.values["voiced_energy"] == pytest.approx(10**-3 * 900)
    assert v0.values["base_db_ms"] == -30.0 * 900 and v0.values["audio_live_ms"] == 1000
    assert v1.values["base_db_ms"] == -30.0 * 500
    assert v2.values["voiced_ms"] == 0 and v2.values["voiced_energy"] == 0
    assert total(vol.tally, "total_ms") == 3000 and total(pause.tally, "total_ms") == 3000
    nobase, _ = vjudge(recs, 3_000, base=None)
    assert total(nobase.tally, "level_voiced_ms") == 0 and total(nobase.tally, "base_db_ms") == 0
    # 기록이 없는 1초는 total_ms 만 센다
    gap, gap_pause = vjudge([vrec(0, -30.0), vrec(2, -30.0)], 3_000)
    assert gap.tally[1].values == {"total_ms": 1000}
    assert gap_pause.tally[1].values == {"total_ms": 1000}


def test_volume_low_ms_counts_seconds_whose_window_is_below():
    recs = [vrec(i, -30.0) for i in range(6)] + [vrec(i, -42.0) for i in range(6, 12)]
    vol, _ = vjudge(recs, 12_000)
    low = [i.t_ms for i in vol.tally if i.values["low_ms"]]
    # 5초 평균이 -6 dB 아래로 내려가는 시점부터 센다
    assert low and low[0] > 6_000 and low[-1] == 11_000
    assert total(vol.tally, "low_ms") == 900 * len(low)


def test_volume_long_silence_counted_once():
    recs = [vrec(i, -30.0) for i in range(3)] + [
        vrec(3 + k, None, silence_ms=(k + 1) * 1000) for k in range(12)
    ]
    _, pause = vjudge(recs, 15_000)
    assert total(pause.tally, "long_silence_count") == 1
    crossed = [i.t_ms for i in pause.tally if i.values["long_silence_count"]]
    assert crossed == [8_000]  # 침묵이 5000 이하에서 6000 이 되는 1초
    # 커서 뒤로 나눠 불러도 한 번만 센다
    a = vjudge(recs, 8_000)[1]
    b = vjudge(recs, 15_000, since=a.counted_until_ms)[1]
    check_shape(b, since=a.counted_until_ms)
    assert total(a.tally, "long_silence_count") + total(b.tally, "long_silence_count") == 1


def test_volume_baseline():
    levels = [-30.0 - (i % 3) for i in range(14)]
    assert volume.baseline(levels) is None
    assert volume.baseline([*levels, -31.0]) == -31.0
    assert volume.baseline([-20.0] * 15 + [-60.0] * 5) == -20.0  # 처음 15개만


def test_volume_summarize_and_criteria():
    v = {
        "voiced_ms": 1800,
        "voiced_energy": 10**-3 * 1800,
        "low_ms": 900,
        "level_voiced_ms": 1800,
        "base_db_ms": -28.0 * 1800,
        "audio_live_ms": 2000,
        "total_ms": 2000,
    }
    pause = {"long_silence_count": 1, "audio_live_ms": 2000, "total_ms": 2400}
    s = volume.summarize({"VOLUME": v, "PAUSE": pause})
    assert s["VOLUME"]["level_db"] == -30.0 and s["VOLUME"]["voice_diff_db"] == -2.0
    assert s["VOLUME"]["low_ratio"] == 0.5 and s["VOLUME"]["measured_ratio"] == 1.0
    assert s["PAUSE"]["long_silence_count"] == 1
    assert s["PAUSE"]["measured_ratio"] == pytest.approx(0.8333, abs=1e-4)
    empty = volume.summarize({})
    assert empty["VOLUME"]["level_db"] is None and empty["PAUSE"]["measured_ratio"] is None
    c = volume.criteria()
    assert c["VOLUME_LOW"].direction == "LOWER_IS_WORSE"
    assert (c["VOLUME_LOW"].threshold, c["VOLUME_LOW"].bad) == (-6.0, -15.0)
    assert (c["LONG_SILENCE"].onset_lag_ms, c["LONG_SILENCE"].offset_lag_ms) == (5000, 0)
