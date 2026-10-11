"""폴더 위치가 설치 방식이나 실행 위치와 상관없이 이 프로젝트를 가리키는지 확인한다."""

from __future__ import annotations

import tomllib

from coach_lab.paths import OUTPUTS_DIR, PROJECT_ROOT, REPLAY_DIR, RESULTS_DIR, SCENARIOS_DIR


def test_project_root_is_this_project():
    pyproject = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert pyproject["project"]["name"] == "coach-agent"


def test_paths_stay_inside_the_project():
    for path in (SCENARIOS_DIR, OUTPUTS_DIR, REPLAY_DIR, RESULTS_DIR):
        assert path.is_relative_to(PROJECT_ROOT)
