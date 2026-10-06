"""실험의 핵심 지표를 `reports/results/<이름>.json` 으로 남긴다. 보고서 노트북은 이 파일을 읽어 보여 준다."""

import json
from pathlib import Path

import pandas as pd

from script_coverage.version import FEATURE_VERSION

from .paths import RESULTS_DIR


def _plain(value):
    """numpy · pandas 값을 JSON 으로 쓸 수 있는 파이썬 값으로 바꾼다."""
    if hasattr(value, "item"):
        return value.item()
    raise TypeError(f"JSON 으로 쓸 수 없는 값: {type(value).__name__}")


def write_result(name: str, model: str, metrics: dict) -> Path:
    """기능 버전과 모델 이름을 함께 적어 저장한다."""
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    path = RESULTS_DIR / f"{name}.json"
    payload = {"feature_version": FEATURE_VERSION, "model": model, **metrics}
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=_plain), encoding="utf-8"
    )
    return path


def read_result(name: str) -> dict:
    return json.loads((RESULTS_DIR / f"{name}.json").read_text(encoding="utf-8"))


def result_table(name: str, key: str) -> pd.DataFrame:
    """저장한 지표 중 `{행: {열: 값}}` 모양인 항목을 표(DataFrame)로 읽는다. 보고서 노트북용."""
    return pd.DataFrame(read_result(name)[key]).T
