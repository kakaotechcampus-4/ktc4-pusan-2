"""연구용 판정 대역 패키지 — 네 모듈이 묶음으로 나오는지."""

from __future__ import annotations

from coach_lab.judges import lab_judges, real_judges, volume
from pitch_coach_ai.features import filler as real_filler
from pitch_coach_ai.features import gaze as real_gaze
from pitch_coach_ai.features import pace as real_pace
from pitch_coach_ai.features import volume as real_volume
from pitch_coach_ai.features.coach.judges import Judges


def test_real_judges_bundles_the_four_service_modules():
    """재생 · 실험 묶음에는 대역이 없다 — 판정 모듈 넷과 기준 음량 둘 다 서비스 것이다."""
    bundle = real_judges()
    assert bundle.gaze is real_gaze.core
    assert bundle.pace is real_pace.core
    assert bundle.volume is real_volume.core
    assert bundle.filler is real_filler.core
    assert bundle.speaking_levels is real_volume.core.speaking_levels
    assert bundle.baseline is real_volume.core.baseline


def test_lab_judges_bundles_the_four_modules():
    bundle = lab_judges()
    assert isinstance(bundle, Judges)
    for mod in (bundle.gaze, bundle.pace, bundle.volume, bundle.filler):
        assert callable(mod.judge) and callable(mod.summarize) and callable(mod.criteria)
    assert bundle.baseline is volume.baseline
