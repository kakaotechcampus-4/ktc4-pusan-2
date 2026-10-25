"""LLM 설정 해시와 evaluate_take 의 입력 검사.

설정 해시는 archive 노트북이 남긴 캐시를 그대로 쓰기 위해 노트북과 같은 식이어야 한다.
"""

import hashlib
import json

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
from tests.unit.fakes import FakeLLM, KeepFirstVerifier, deck_rubrics


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


def test_evaluate_take_evaluates_present_rubric_and_flags_only_missing_one():
    """평가 기준이 있는 장은 채점하고(LLM 은 그 장만 부른다), 없는 장만 RUBRIC_MISSING 이다."""
    [rubric] = deck_rubrics((6,))
    take = Take(
        script_name="s",
        take_id="T",
        slides=[
            SlideSTT(slide_number=6, stt="시범 운영을 했다"),
            SlideSTT(slide_number=9, stt="말"),
        ],
    )
    eval_llm = FakeLLM(SemanticEvaluation(sentences=[], name_checks=[]))
    verifier = KeepFirstVerifier()
    evaluations, stats = evaluate_take(
        take, {6: rubric}, eval_llm=eval_llm, verifier_llm=verifier, model="m"
    )
    assert [e.slide_number for e in evaluations] == [6]
    assert stats["failed"] == {9: "RUBRIC_MISSING"}
    assert eval_llm.calls == 1 and stats["semantic_calls"] == 1


def test_evaluate_take_skips_slide_without_rubric():
    """평가 기준이 없는 장은 예외 없이 건너뛰고 RUBRIC_MISSING 으로 남긴다 (LLM 은 부르지 않는다)."""
    take = Take(script_name="s", take_id="T", slides=[SlideSTT(slide_number=1, stt="말했다")])
    evaluations, stats = evaluate_take(take, {}, eval_llm=None, verifier_llm=None, model="m")
    assert evaluations == []
    assert stats["failed"] == {1: "RUBRIC_MISSING"}
    assert stats["causes"] == {1: "평가 기준 없음"}
    assert stats["semantic_calls"] == 0
