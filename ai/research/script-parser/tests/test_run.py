"""연구 폴더 준비 확인 (LLM 호출 없음). 이 폴더의 기본 실행(`uv run python -m pytest`)이 고르는 시험이다."""

import run
from pitch_coach_ai.features import script_parser


def test_run_uses_the_service_core():
    # 서비스를 editable 로 설치하지 않았으면 `import run` 에서 이미 실패한다
    assert run.parse_script is script_parser.parse_script


def test_every_script_has_a_label_and_every_label_a_script():
    scripts = {p.stem for p in run.SCRIPTS_DIR.glob("*.txt")}
    labels = {p.stem for p in run.LABELS_DIR.glob("*.json")}
    assert scripts
    assert scripts == labels
