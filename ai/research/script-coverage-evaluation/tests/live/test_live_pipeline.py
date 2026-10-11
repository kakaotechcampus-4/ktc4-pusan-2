"""실제 LLM API 로 슬라이드 한 장을 대본 분석 → STT 평가까지 돌린다 (과금, 약 4~5회 호출).

캐시 재생 비교는 캐시에서 응답을 꺼내므로 LLM 클라이언트를 실제로 부르지 않는다.
이 테스트는 구조화 출력 LLM 을 만들고 부르는 경로가 실제로 동작하는지 확인한다.
기본 실행에서 빠진다 — `uv run python -m pytest -m live` 로 따로 돌린다.
"""

import pytest

from coverage_lab.datasets import load_script_json, load_take
from coverage_lab.llm import load_settings, script_llms, stt_llms
from coverage_lab.paths import SCRIPT_DIR, STT_DIR
from script_coverage.script_analysis.core import analyze_script
from script_coverage.stt_evaluation.core import evaluate_take

pytestmark = pytest.mark.live

SCRIPT = "가상대본1"
TAKE = "가상대본1_take1"
SLIDE = 6  # 날짜 · 기간 · 퍼센트 수치가 고루 있는 슬라이드


@pytest.fixture(scope="module")
def settings():
    return load_settings()


@pytest.fixture(scope="module")
def rubric(settings):
    slides = [s for s in load_script_json(SCRIPT_DIR / f"{SCRIPT}.json") if s.slide_number == SLIDE]
    rubrics, stats = analyze_script(SCRIPT, slides, **script_llms(settings), model=settings.model)
    assert stats["failed"] == {}
    assert (stats["analysis_calls"], stats["final_calls"], stats["unit_calls"]) == (1, 1, 1)
    assert len(rubrics) == 1
    return rubrics[0]


def test_script_analysis_live(rubric, settings):
    assert rubric.meta.llm_model == settings.model
    assert rubric.key_points, "Key Point 가 하나도 없다"
    assert rubric.content_units, "전달 단위가 하나도 없다"
    assert sum(role == "claim" for role in rubric.sentence_roles) <= 1
    assert any(f.source == "rule" for f in rubric.critical_facts)


def test_stt_evaluation_live(rubric, settings):
    take = load_take(STT_DIR / f"{TAKE}.json")
    take = take.model_copy(update={"slides": [s for s in take.slides if s.slide_number == SLIDE]})
    evaluations, stats = evaluate_take(
        take, {SLIDE: rubric}, **stt_llms(settings), model=settings.model
    )
    assert stats["failed"] == {}
    assert stats["semantic_calls"] == 1
    assert len(evaluations) == 1
    evaluation = evaluations[0]
    assert len(evaluation.sentences) == len(rubric.sentences)
    assert 0.0 <= evaluation.scores["content_coverage"] <= 1.0
    assert {s.status for s in evaluation.sentences} <= {
        "said",
        "partial",
        "missing",
        "contradicted",
    }
