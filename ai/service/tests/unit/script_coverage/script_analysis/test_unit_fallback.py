"""전달 단위 단계 실패 복구: 그 단계만 실패한 장은 문장 단위 평가 기준(sentence_fallback)으로 낸다."""

import pytest

from pitch_coach_ai.features.script_coverage.core import ScriptLLMs, analyze_script
from pitch_coach_ai.features.script_coverage.script_analysis import core as analysis_core
from pitch_coach_ai.features.script_coverage.script_analysis.core import (
    analyze_script as analyze_slides,
)
from pitch_coach_ai.features.script_coverage.script_analysis.schemas import SlideScript

from ..fakes import DeckAwareLLM, FakeLLM
from ..shared.slides import SLIDES

MODEL = "test-model"


def _slides(*numbers):
    return [SlideScript(slide_number=n, script=SLIDES[("가상대본1", n)]) for n in numbers]


class _Failing(DeckAwareLLM):
    """DeckAwareLLM 처럼 답하되 예외 객체를 주면 늘 그 예외를 낸다."""

    def __init__(self, stage, error=None):
        super().__init__(stage)
        self.error = error

    def invoke(self, messages):
        if self.error is not None:
            raise self.error
        return super().invoke(messages)


def _run(numbers=(4, 6, 7), **errors):
    llms = {s: _Failing(s, errors.get(s)) for s in ("semantic", "final", "unit")}
    rubrics, stats = analyze_slides(
        "대본", _slides(*numbers), **{f"{s}_llm": llm for s, llm in llms.items()}, model=MODEL
    )
    return rubrics, stats


def test_unit_stage_failure_recovers_with_sentence_units():
    normal, _ = _run()
    rubrics, stats = _run(unit=TimeoutError("대본 원문"))
    assert [r.meta.slide_number for r in rubrics] == [4, 6, 7]
    assert stats["failed"] == {} and stats["causes"] == {}
    assert stats["unit_fallback"] == {4: "LLM_TIMEOUT", 6: "LLM_TIMEOUT", 7: "LLM_TIMEOUT"}
    for rubric in rubrics:
        assert rubric.meta.rubric_quality == "sentence_fallback"
        assert "unit_fallback:LLM_TIMEOUT" in rubric.warnings
        assert rubric.meta.unit_config_hash == ""
        assert rubric.content_units
        assert {u.source for u in rubric.content_units} == {"sentence"}
    assert all(r.meta.rubric_quality == "full" for r in normal)
    assert "unit_fallback" not in _run()[1]


def test_recovered_rubric_id_differs_from_normal_one():
    normal, _ = _run()
    recovered, _ = _run(unit=TimeoutError("x"))
    for a, b in zip(normal, recovered, strict=True):
        assert a.meta.slide_number == b.meta.slide_number
        assert a.meta.rubric_id != b.meta.rubric_id


def test_error_code_follows_llm_error_mapping():
    _, stats = _run((6,), unit=ValueError("x"))
    assert stats["unit_fallback"] == {6: "LLM_ERROR"}


@pytest.mark.parametrize("stage", ["semantic", "final"])
def test_earlier_stage_failure_is_still_a_failure(stage):
    rubrics, stats = _run((6,), **{stage: TimeoutError("x")})
    assert rubrics == []
    assert stats["failed"] == {6: "LLM_TIMEOUT"}
    assert "unit_fallback" not in stats


def test_disabled_constant_keeps_old_behavior(monkeypatch):
    monkeypatch.setattr(analysis_core, "UNIT_FALLBACK_ENABLED", False)
    rubrics, stats = _run(unit=TimeoutError("x"))
    assert rubrics == []
    assert stats["failed"] == {4: "LLM_TIMEOUT", 6: "LLM_TIMEOUT", 7: "LLM_TIMEOUT"}
    assert stats["causes"][4] == "전달 단위 TimeoutError"
    assert "unit_fallback" not in stats


def test_mixed_stage_failures_recover_only_unit_failure():
    class UnitFailsOn7(DeckAwareLLM):
        def invoke(self, messages):
            if "국내 공공도서관은" in messages[1][1]:
                raise TimeoutError("x")
            return super().invoke(messages)

    class FinalFailsOn4(DeckAwareLLM):
        def invoke(self, messages):
            if "예측 모델은" in messages[1][1]:
                raise ValueError("x")
            return super().invoke(messages)

    rubrics, stats = analyze_slides(
        "대본", _slides(4, 6, 7), semantic_llm=DeckAwareLLM("semantic"),
        final_llm=FinalFailsOn4("final"), unit_llm=UnitFailsOn7("unit"), model=MODEL,
    )  # fmt: skip
    assert [r.meta.slide_number for r in rubrics] == [6, 7]
    assert stats["failed"] == {4: "LLM_ERROR"}
    assert stats["unit_fallback"] == {7: "LLM_TIMEOUT"}
    assert [r.meta.rubric_quality for r in rubrics] == ["full", "sentence_fallback"]


def test_top_level_response_has_no_failed_entry_for_recovered_slide():
    llms = ScriptLLMs(
        semantic=DeckAwareLLM("semantic"),
        final=DeckAwareLLM("final"),
        unit=FakeLLM(TimeoutError("x")),
    )
    request = {
        "pitch_id": "p-1",
        "script_version_id": "sv-3",
        "slides": [{"slide_number": n, "script": SLIDES[("가상대본1", n)]} for n in (4, 6)],
        "slide_numbers": None,
    }
    response = analyze_script(request, llms=llms, model=MODEL)
    assert response.failed == []
    assert [r.meta.slide_number for r in response.rubrics] == [4, 6]
    assert {r.meta.rubric_quality for r in response.rubrics} == {"sentence_fallback"}


def test_normal_rubric_id_changes_only_by_schema_version(monkeypatch):
    """정상 평가 기준의 id 는 형식 버전 외 입력이 같으면 같다 (복구 경로가 정상 경로를 바꾸지 않는다)."""
    from pitch_coach_ai.features.script_coverage.script_analysis import draft

    first, _ = _run((6,))
    second, _ = _run((6,))
    assert first[0].meta.rubric_id == second[0].meta.rubric_id
    monkeypatch.setattr(draft, "RUBRIC_SCHEMA_VERSION", "9.9")
    other, _ = _run((6,))
    assert other[0].meta.rubric_id != first[0].meta.rubric_id
