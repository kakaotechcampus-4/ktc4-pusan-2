"""연구 폴더 준비 확인 (LLM 호출 없음). 이 폴더의 기본 실행(`uv run python -m pytest`)이 고르는 시험이다."""

from pathlib import Path

import run
from pitch_coach_ai.features import script_parser


def test_run_uses_the_service_core_in_this_repo():
    # 서비스를 설치하지 않았으면 `import run` 에서 이미 실패한다. 다른 사본이 아니라 저장소 ai/service 의
    # 코어를 쓰는지(editable 설치)는 모듈 파일 위치로 본다
    assert run.parse_script is script_parser.parse_script
    service_dir = run.ROOT.parents[1] / "service"
    assert Path(script_parser.__file__).resolve().is_relative_to(service_dir)


def test_every_script_has_a_label_and_every_label_a_script():
    scripts = {p.stem for p in run.SCRIPTS_DIR.glob("*.txt")}
    labels = {p.stem for p in run.LABELS_DIR.glob("*.json")}
    assert scripts
    assert scripts == labels
