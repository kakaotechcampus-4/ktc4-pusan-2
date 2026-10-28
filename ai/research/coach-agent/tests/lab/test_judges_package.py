"""연구용 판정 대역 패키지 — 네 모듈이 묶음으로 나오는지."""

from __future__ import annotations

from coach_lab.judges import lab_judges, real_judges, volume
from pitch_coach_ai.features.coach.judges import Judges
from pitch_coach_ai.features.filler import core as real_filler
from pitch_coach_ai.features.gaze import core as real_gaze
from pitch_coach_ai.features.pace import core as real_pace
from pitch_coach_ai.features.volume import core as real_volume


def test_real_judges_bundles_the_four_service_modules():
    """재생 · 실험 묶음에는 대역이 없다 — 판정 모듈 넷과 기준 음량 둘 다 서비스 것이다."""
    bundle = real_judges()
    assert bundle.gaze is real_gaze
    assert bundle.pace is real_pace
    assert bundle.volume is real_volume
    assert bundle.filler is real_filler
    assert bundle.speaking_levels is real_volume.speaking_levels
    assert bundle.baseline is real_volume.baseline


def test_lab_judges_bundles_the_four_modules():
    bundle = lab_judges()
    assert isinstance(bundle, Judges)
    for mod in (bundle.gaze, bundle.pace, bundle.volume, bundle.filler):
        assert callable(mod.judge) and callable(mod.summarize) and callable(mod.criteria)
    assert bundle.baseline is volume.baseline
