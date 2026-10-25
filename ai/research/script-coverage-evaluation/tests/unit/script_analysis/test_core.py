from script_coverage.script_analysis.core import analyze_script
from script_coverage.script_analysis.schemas import SlideScript
from tests.unit.fakes import empty_script_llms
from tests.unit.shared.slides import SLIDES


def test_analyze_script_empty_slide_needs_no_llm():
    """읽을 내용이 없는 슬라이드는 LLM 을 부르지 않는다 (LLM 인자는 None 이어도 된다)."""
    rubrics, stats = analyze_script(
        "빈대본",
        [SlideScript(slide_number=1, script="")],
        semantic_llm=None,
        final_llm=None,
        unit_llm=None,
        model="test-model",
    )
    assert stats == {
        "slides": 1,
        "analysis_calls": 0,
        "final_calls": 0,
        "unit_calls": 0,
        "failed": {},
        "causes": {},
    }
    assert len(rubrics) == 1
    assert rubrics[0].meta.script_name == "빈대본"
    assert rubrics[0].meta.slide_number == 1
    assert rubrics[0].key_points == []


def test_analyze_script_orders_slides_by_number():
    """슬라이드는 받은 순서와 상관없이 번호순으로 분석한다 (노트북이 대본을 읽을 때 정렬하던 것과 같다)."""
    slides = [SlideScript(slide_number=2, script=""), SlideScript(slide_number=1, script="")]
    rubrics, _ = analyze_script(
        "빈대본", slides, semantic_llm=None, final_llm=None, unit_llm=None, model="test-model"
    )
    assert [r.meta.slide_number for r in rubrics] == [1, 2]


def _slides(*numbers):
    return [SlideScript(slide_number=n, script=SLIDES[("가상대본1", n)]) for n in numbers]


def _run(slides, **kwargs):
    semantic, final, unit = empty_script_llms()
    rubrics, stats = analyze_script(
        "대본", slides, semantic_llm=semantic, final_llm=final, unit_llm=unit,
        model="test-model", **kwargs,
    )  # fmt: skip
    return rubrics, stats, (semantic, final, unit)


def test_targets_limit_llm_calls_and_results():
    """대상이 아닌 장은 LLM 을 부르지 않고 결과 · 통계에도 없다."""
    rubrics, stats, llms = _run(_slides(4, 6, 7), targets=[6])
    assert [r.meta.slide_number for r in rubrics] == [6]
    assert stats["slides"] == 1
    assert [llm.calls for llm in llms] == [1, 1, 1]


def test_targets_none_means_every_slide():
    rubrics, stats, _ = _run(_slides(4, 6, 7), targets=None)
    assert [r.meta.slide_number for r in rubrics] == [4, 6, 7]
    assert stats["slides"] == 3


def test_empty_targets_make_nothing():
    rubrics, stats, llms = _run(_slides(4, 6), targets=[])
    assert rubrics == [] and stats["slides"] == 0 and stats["failed"] == {}
    assert [llm.calls for llm in llms] == [0, 0, 0]


def test_targets_keep_every_slide_as_keyword_context():
    """키워드(TF-IDF)는 대상이 아닌 장까지 포함한 전체로 낸다. 대상 장만 넣었을 때와 다르다."""
    all_rubrics, _, _ = _run(_slides(4, 6, 7))
    picked, _, _ = _run(_slides(4, 6, 7), targets=[6])
    alone, _, _ = _run(_slides(6))
    expected = next(r for r in all_rubrics if r.meta.slide_number == 6)
    assert picked[0].keywords == expected.keywords
    assert picked[0].keywords != alone[0].keywords
    assert picked[0].meta.rubric_id == expected.meta.rubric_id


def test_failed_slides_hold_error_codes_and_causes():
    semantic, final, unit = empty_script_llms(semantic=TimeoutError("대본 원문이 든 메시지"))
    rubrics, stats = analyze_script(
        "대본", _slides(4, 6), semantic_llm=semantic, final_llm=final, unit_llm=unit,
        model="test-model",
    )  # fmt: skip
    assert rubrics == []
    assert stats["failed"] == {4: "LLM_TIMEOUT", 6: "LLM_TIMEOUT"}
    assert stats["causes"] == {4: "1차 분석 TimeoutError", 6: "1차 분석 TimeoutError"}
    assert "원문" not in str(stats["causes"])


def test_rubric_has_no_clock():
    first, _, _ = _run(_slides(6))
    second, _, _ = _run(_slides(6))
    assert first[0].model_dump() == second[0].model_dump()
    assert "created_at" not in first[0].meta.model_dump()
