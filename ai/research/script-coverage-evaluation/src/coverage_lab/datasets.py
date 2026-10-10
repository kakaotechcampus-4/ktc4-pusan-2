"""데이터 읽기: 대본 JSON 과 연습(take)의 STT JSON."""

import json
from pathlib import Path

from script_coverage.script_analysis.schemas import SlideScript
from script_coverage.stt_evaluation.schemas import Take

from .paths import SCRIPT_DIR, STT_DIR


def load_script_json(path: Path) -> list[SlideScript]:
    """대본 JSON 을 읽는다. 형식: [{"slide_number": 1, "script": "..."}, ...]"""
    items = json.loads(path.read_text(encoding="utf-8"))
    return sorted(
        (SlideScript.model_validate(item) for item in items), key=lambda s: s.slide_number
    )


def script_files(directory: Path = SCRIPT_DIR) -> list[Path]:
    """가상 대본: JSON 파일 하나가 발표 하나다. 파일 이름(확장자 제외)으로 결과를 저장 · 조회한다."""
    return sorted(directory.glob("*.json"))


def load_take(path: Path) -> Take:
    return Take.model_validate_json(path.read_text(encoding="utf-8"))


def take_files(directory: Path = STT_DIR) -> list[Path]:
    return sorted(directory.glob("*.json"))
