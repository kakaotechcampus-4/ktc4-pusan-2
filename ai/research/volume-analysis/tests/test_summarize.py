"""summarize · criteria — Take 지표 계산과 판정 기준 (volume.md 5-3)."""

from __future__ import annotations

import math

import pytest

from volume.config import Config
from volume.core import criteria, summarize


def test_volume_metrics() -> None:
    tally = {
        "VOLUME": {
            "voiced_ms": 2000,
            "voiced_energy": 10**-3 * 1000 + 10**-4 * 1000,
            "low_ms": 500,
            "level_voiced_ms": 1500,
            "base_db_ms": -30.0 * 1500,
            "audio_live_ms": 9000,
            "total_ms": 10000,
        }
    }
    v = summarize(tally).VOLUME
    level = 10 * math.log10((10**-3 + 10**-4) / 2)
    assert v.level_db == pytest.approx(level, abs=0.01)
    assert v.voice_diff_db == pytest.approx(level + 30.0, abs=0.01)
    assert v.low_ratio == pytest.approx(0.25)
    # min(9000/10000, 1500/2000)
    assert v.measured_ratio == pytest.approx(0.75)


def test_pause_metrics() -> None:
    p = summarize({"PAUSE": {"long_silence_count": 2, "audio_live_ms": 990, "total_ms": 1000}}).PAUSE
    assert p.long_silence_count == 2
    assert p.measured_ratio == pytest.approx(0.99)


def test_empty_tally() -> None:
    s = summarize({})
    assert (s.VOLUME.level_db, s.VOLUME.voice_diff_db, s.VOLUME.low_ratio) == (None, None, None)
    assert s.VOLUME.measured_ratio == 0.0
    assert s.PAUSE.long_silence_count == 0
    assert s.PAUSE.measured_ratio == 0.0


def test_no_voice_means_volume_measured_zero() -> None:
    s = summarize({"VOLUME": {"audio_live_ms": 10000, "total_ms": 10000}})
    assert s.VOLUME.measured_ratio == 0.0
    assert s.VOLUME.level_db is None


def test_no_baseline_means_no_diff() -> None:
    s = summarize({"VOLUME": {"voiced_ms": 1000, "voiced_energy": 10**-3 * 1000, "audio_live_ms": 1000, "total_ms": 1000}})
    assert s.VOLUME.level_db == pytest.approx(-30.0)
    assert s.VOLUME.voice_diff_db is None
    assert s.VOLUME.measured_ratio == 0.0


def test_criteria_follow_config() -> None:
    cfg = Config(low_diff_db=-5.0, long_silence_ms=4000)
    c = criteria(cfg)
    assert c.VOLUME_LOW.metric == "voice_diff_db"
    assert c.VOLUME_LOW.direction == "LOWER_IS_WORSE"
    assert c.VOLUME_LOW.threshold == -5.0
    assert c.VOLUME_LOW.bad == cfg.low_diff_bad_db
    assert c.LONG_SILENCE.metric == "silence_ms"
    assert c.LONG_SILENCE.direction == "HIGHER_IS_WORSE"
    assert c.LONG_SILENCE.threshold == 4000
    assert c.LONG_SILENCE.onset_lag_ms == cfg.silence_onset_lag_ms
