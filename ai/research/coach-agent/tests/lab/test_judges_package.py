"""연구용 판정 대역 패키지 — 네 모듈이 묶음으로 나오는지."""

from __future__ import annotations

from coach.judges import Judges
from coach_lab.judges import lab_judges, volume


def test_lab_judges_bundles_the_four_modules():
    bundle = lab_judges()
    assert isinstance(bundle, Judges)
    for mod in (bundle.gaze, bundle.pace, bundle.volume, bundle.filler):
        assert callable(mod.judge) and callable(mod.summarize) and callable(mod.criteria)
    assert bundle.baseline is volume.baseline
