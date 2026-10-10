"""연구용 판정 대역 패키지 — 네 모듈이 모두 나오는지."""

from __future__ import annotations

from coach_lab.judges import lab_judges, volume


def test_lab_judges_lists_the_four_modules():
    mods = lab_judges()
    assert set(mods) == {"gaze", "pace", "volume", "filler"}
    for mod in mods.values():
        assert callable(mod.judge) and callable(mod.summarize) and callable(mod.criteria)
    assert callable(volume.baseline)
