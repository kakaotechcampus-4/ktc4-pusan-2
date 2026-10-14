"""common/meta.py: feature_version(그대로) · criteria_version 해시 · model(기본 결과면 null)."""

import hashlib
import json

from pydantic import BaseModel

from pitch_coach_ai.common.llm import StructuredLlm
from pitch_coach_ai.common.meta import (
    SCHEMA_VERSION,
    build_judge_meta,
    build_meta,
    config_hash,
    criteria_version,
)


class _Config(BaseModel):
    threshold: float = 0.5
    window_ms: int = 30_000


def _llm_that_answered(model: str) -> StructuredLlm:
    llm = StructuredLlm(object(), role="default", timeout_seconds=1, max_retries=0)  # type: ignore[arg-type]
    llm.responded_model = model
    return llm


def test_meta_without_llm_has_null_model() -> None:
    meta = build_meta("sample-0.1")
    assert meta.model_dump() == {
        "schema_version": SCHEMA_VERSION,
        "feature_version": "sample-0.1",
        "model": None,
    }
    assert SCHEMA_VERSION == "1.0"


def test_feature_version_is_used_as_is_without_extra_prefix() -> None:
    """version.py 의 값이 이미 `<모듈>-<버전>` 이다 (architecture.md 5절).

    접두어를 또 붙이지 않는다.
    """
    assert build_meta("coach-1.2").feature_version == "coach-1.2"


def test_meta_carries_the_model_that_actually_answered() -> None:
    meta = build_meta("script_parser-1.0", llm=_llm_that_answered("openai/gpt-6-luna-0612"))
    assert meta.model == "openai/gpt-6-luna-0612"


def test_fallback_result_has_null_model_even_if_llm_answered() -> None:
    meta = build_meta("review-1.0", llm=_llm_that_answered("openai/gpt-6-luna"), fallback=True)
    assert meta.model is None


def test_criteria_version_changes_only_when_config_changes() -> None:
    base = criteria_version("sample-0.1", _Config())
    assert base.startswith("sample-0.1+")
    assert len(base.split("+")[1]) == 12
    assert criteria_version("sample-0.1", _Config()) == base
    assert criteria_version("sample-0.1", _Config(threshold=0.6)) != base


def test_config_hash_matches_the_coach_research_formula() -> None:
    """코치 연구 코드와 같은 방식이어야 같은 기준에 같은 해시가 나온다."""
    config = _Config()
    blob = json.dumps(config.model_dump(mode="json"), sort_keys=True, ensure_ascii=False)
    assert config_hash(config) == hashlib.sha256(blob.encode("utf-8")).hexdigest()[:12]


def test_judge_meta_has_criteria_versions() -> None:
    versions = {"sample": criteria_version("sample-0.1", _Config())}
    meta = build_judge_meta("sample-0.1", versions)
    dumped = meta.model_dump()
    assert dumped["criteria_versions"] == versions
    assert dumped["feature_version"] == "sample-0.1"
    assert dumped["model"] is None
