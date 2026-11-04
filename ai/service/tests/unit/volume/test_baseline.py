"""speaking_levels · baseline — 기준 음량 표본 고르기와 기준 음량 잡기 (volume.md 5-2 · 6-2)."""

from __future__ import annotations

from typing import Any

from pitch_coach_ai.features.volume.config import Config
from pitch_coach_ai.features.volume.core import baseline, speaking_levels


def rec(
    t_ms: int, level_db: float | None = -30.0, voiced_ms: int = 800, audio_live: bool = True
) -> dict[str, Any]:
    return {
        "t_ms": t_ms,
        "duration_ms": 1000,
        "level_db": level_db,
        "voiced_ms": voiced_ms,
        "silence_ms": 0,
        "audio_live": audio_live,
    }


def test_speaking_levels_keeps_only_spoken_seconds() -> None:
    # volume.md 5-5 예시: 둘째는 말한 시간이 기준 미만, 셋째는 오디오가 끊겼다
    records = [rec(0, -29.5, 820), rec(1000, -40.1, 120), rec(2000, None, 0, audio_live=False)]
    assert speaking_levels(records) == [-29.5]


def test_speaking_levels_drops_null_level_and_dead_audio() -> None:
    cfg = Config()
    records = [
        rec(0, None, cfg.voiced_min_ms),
        rec(1000, -31.0, cfg.voiced_min_ms, audio_live=False),
        rec(2000, -32.0, cfg.voiced_min_ms),
    ]
    assert speaking_levels(records) == [-32.0]


def test_speaking_levels_in_time_order_first_wins() -> None:
    records = [rec(2000, -28.0), rec(0, -30.0), rec(1000, -29.0), rec(1000, -50.0)]
    assert speaking_levels(records) == [-30.0, -29.0, -28.0]


def test_speaking_levels_empty() -> None:
    assert speaking_levels([]) == []


def test_baseline_not_enough_samples() -> None:
    cfg = Config()
    assert baseline([-30.0] * (cfg.baseline_samples - 1)) is None
    assert baseline([]) is None


def test_baseline_enough_samples_is_median_of_first() -> None:
    cfg = Config()
    n = cfg.baseline_samples
    levels = [-30.0 - i for i in range(n)]
    expected = levels[n // 2] if n % 2 else (levels[n // 2 - 1] + levels[n // 2]) / 2
    assert baseline(levels) == expected


def test_baseline_ignores_samples_after_first_n() -> None:
    cfg = Config()
    n = cfg.baseline_samples
    assert baseline([-30.0] * n + [-60.0] * 100) == -30.0


def test_baseline_resists_outlier() -> None:
    cfg = Config()
    levels = [-30.0] * (cfg.baseline_samples - 1) + [-5.0]
    assert baseline(levels) == -30.0


def test_baseline_follows_config() -> None:
    assert baseline([-30.0, -32.0, -34.0], Config(baseline_samples=3)) == -32.0
