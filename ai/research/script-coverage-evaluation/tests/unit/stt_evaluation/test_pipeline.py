"""LLM 설정 해시와 evaluate_take 의 입력 검사.

설정 해시는 archive 노트북이 남긴 캐시를 그대로 쓰기 위해 노트북과 같은 식이어야 한다.
"""

import hashlib
import json

import pytest

from script_coverage.stt_evaluation.core import evaluate_take
from script_coverage.stt_evaluation.judge import eval_config_hash
from script_coverage.stt_evaluation.prompts.semantic import SEMANTIC_EVAL_PROMPT
from script_coverage.stt_evaluation.prompts.verifier import VERIFIER_PROMPT
from script_coverage.stt_evaluation.schemas import (
    SemanticEvaluation,
    SlideSTT,
    Take,
    VerifierResult,
)
from script_coverage.stt_evaluation.verify import verifier_config_hash


def _notebook_hash(model, prompt, schema):
    # 노트북 eval_config_hash / verifier_config_hash 의 식 그대로
    payload = json.dumps(
        {"model": model, "prompt": prompt, "schema": schema}, ensure_ascii=False, sort_keys=True
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


def test_eval_config_hash_matches_notebook_formula():
    expected = _notebook_hash(
        "test-model", SEMANTIC_EVAL_PROMPT, SemanticEvaluation.model_json_schema()
    )
    assert eval_config_hash("test-model") == expected == "af809b067482"


def test_verifier_config_hash_matches_notebook_formula():
    expected = _notebook_hash("test-model", VERIFIER_PROMPT, VerifierResult.model_json_schema())
    assert verifier_config_hash("test-model") == expected == "749ff470009c"


def test_config_hash_depends_on_model():
    assert eval_config_hash("a") != eval_config_hash("b")


def test_evaluate_take_requires_rubric_for_every_slide():
    take = Take(script_name="s", take_id="T", slides=[SlideSTT(slide_number=1, stt="말했다")])
    with pytest.raises(ValueError, match="슬라이드 1 의 평가 기준이 없다"):
        evaluate_take(take, {}, eval_llm=None, verifier_llm=None, model="m")
