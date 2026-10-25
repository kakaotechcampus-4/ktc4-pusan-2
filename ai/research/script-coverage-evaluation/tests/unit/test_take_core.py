"""최상위 진입 함수 `core.evaluate_take` (`/takes/analyze`) · `core.rescore_evaluation` (`/takes/rescore`)."""

import re

import pytest
from pydantic import ValidationError

from script_coverage.core import (
    CoverageError,
    ScriptLLMs,
    TakeLLMs,
    analyze_script,
    confirmations,
    evaluate_take,
    rescore_evaluation,
    take_scores,
)
from script_coverage.schemas import AnalyzeRequest, RescoreRequest
from script_coverage.shared.llm_step import ERROR_MESSAGES
from script_coverage.stt_evaluation.schemas import (
    NameCheck,
    SemanticEvaluation,
    SentenceJudgment,
    SimilarItem,
    UnitJudgment,
    VerifierResult,
)
from script_coverage.version import FEATURE_VERSION, SCHEMA_VERSION
from tests.unit.fakes import FakeLLM
from tests.unit.script_analysis.test_core import _DeckAwareLLM
from tests.unit.shared.slides import SLIDES

MODEL = "test-model"
NUMBERS = (4, 6, 7)


def _script(number: int) -> str:
    return SLIDES[("가상대본1", number)]


class SaidAllLLM:
    """대본 문장의 전달 단위를 모두 말했다고 판정하는 가짜 의미 평가 LLM. `failing` 의 장은 예외를 낸다."""

    def __init__(self, failing: dict[int, BaseException] | None = None):
        self.failing = failing or {}
        self.called_slides: list[int] = []

    @property
    def calls(self) -> int:
        return len(self.called_slides)

    def invoke(self, messages):
        text = messages[1][1]
        number = int(re.match(r"\[슬라이드 (\d+)\]", text).group(1))
        self.called_slides.append(number)
        if number in self.failing:
            raise self.failing[number]
        sentences = [
            SentenceJudgment(
                sentence_id=int(i),
                evidence=[0],
                reason="말했다",
                units=[
                    UnitJudgment(unit_id=u, status="said")
                    for u in re.findall(rf"^\s+- (S{i}-U\d+) ", text, flags=re.M)
                ],
                confidence="high",
            )
            for i in re.findall(r"^\[S(\d+)\]", text, flags=re.M)
        ]
        names = [
            NameCheck(name_id=f"N{n}", said=True, evidence=[0])
            for n in re.findall(r"^- N(\d+) ", text, flags=re.M)
        ]
        return SemanticEvaluation(sentences=sentences, name_checks=names)


def _rubrics(numbers=NUMBERS) -> list:
    """가상대본1 의 평가 기준 (가짜 LLM 으로 만든 것). script_name 은 `sv-3`."""
    llm = {s: _DeckAwareLLM(s) for s in ("semantic", "final", "unit")}
    response = analyze_script(
        {
            "pitch_id": "p-1",
            "script_version_id": "sv-3",
            "slides": [{"slide_number": n, "script": _script(n)} for n in NUMBERS],
            "slide_numbers": list(numbers),
        },
        llms=ScriptLLMs(**llm),
        model=MODEL,
    )
    return response.rubrics


RUBRICS = _rubrics()


def _llms(eval_llm=None, verifier=None) -> TakeLLMs:
    return TakeLLMs(
        eval=eval_llm or SaidAllLLM(), verifier=verifier or FakeLLM(VerifierResult(judgments=[]))
    )


def _request(numbers=NUMBERS, slide_numbers=None, rubrics=None, stt=None) -> dict:
    stt = stt or {}
    return {
        "take_id": "take-123",
        "slides": [{"slide_number": n, "stt": stt.get(n, _script(n))} for n in numbers],
        "rubrics": [r.model_dump() for r in (RUBRICS if rubrics is None else rubrics)],
        "slide_numbers": slide_numbers,
    }


def _analyze(request=None, llms=None):
    return evaluate_take(request or _request(), llms=llms or _llms(), model=MODEL)


def _rubrics_of(*numbers):
    return [r for r in RUBRICS if r.meta.slide_number in numbers]


# ── /takes/analyze ───────────────────────────────────────────


def test_analyze_all_slides_shape():
    response = _analyze()
    assert response.take_id == "take-123"
    assert [e.slide_number for e in response.evaluations] == [4, 6, 7]
    assert {e.script_name for e in response.evaluations} == {"sv-3"}  # 평가 기준의 대본 식별자
    assert response.failed == []
    assert response.take_scores.scored_ratio == 1.0
    assert response.meta.model_dump() == {
        "schema_version": SCHEMA_VERSION,
        "feature_version": FEATURE_VERSION,
        "criteria_versions": {
            "script_coverage": response.meta.criteria_versions["script_coverage"]
        },
        "model": MODEL,
    }
    assert response.meta.criteria_versions["script_coverage"].startswith(f"{FEATURE_VERSION}+")


def test_analyze_slide_numbers_pick_slides():
    eval_llm = SaidAllLLM()
    response = _analyze(_request(slide_numbers=[7, 4]), _llms(eval_llm))
    assert [e.slide_number for e in response.evaluations] == [4, 7]
    assert sorted(eval_llm.called_slides) == [4, 7]  # 6번 장은 부르지 않는다
    assert response.take_scores.scored_ratio == 1.0  # 대상 장 기준


def test_analyze_empty_slide_numbers_is_an_empty_success():
    eval_llm = SaidAllLLM()
    response = _analyze(_request(slide_numbers=[]), _llms(eval_llm))
    assert response.evaluations == [] and response.failed == []
    assert response.take_scores.scored_ratio == 0.0
    assert response.meta.model is None and eval_llm.calls == 0


def test_analyze_same_input_same_output():
    assert _analyze().model_dump(mode="json") == _analyze().model_dump(mode="json")


def test_analyze_missing_rubric_continues_and_counts_in_scored_ratio():
    eval_llm = SaidAllLLM()
    response = _analyze(_request(rubrics=_rubrics_of(4, 7)), _llms(eval_llm))
    assert [e.slide_number for e in response.evaluations] == [4, 7]
    [failure] = response.failed
    assert failure.slide_number == 6
    assert failure.error.code == "RUBRIC_MISSING"
    assert failure.error.message == "6번 장의 평가 기준이 없습니다."
    assert response.take_scores.scored_ratio == pytest.approx(0.667)
    assert 6 not in eval_llm.called_slides


def test_analyze_script_name_from_smallest_slide_rubric():
    response = _analyze(_request(slide_numbers=[7], rubrics=_rubrics_of(7)))
    assert response.evaluations[0].script_name == "sv-3"


def test_analyze_no_rubrics_is_422():
    with pytest.raises(CoverageError) as caught:
        _analyze(_request(rubrics=[]))
    error = caught.value
    assert (error.status_code, error.code) == (422, "RUBRIC_MISSING")
    assert error.message == "채점할 장의 평가 기준이 없습니다."


def test_analyze_all_targets_missing_rubric_is_422():
    with pytest.raises(CoverageError) as caught:
        _analyze(_request(slide_numbers=[6, 7], rubrics=_rubrics_of(4)))
    assert (caught.value.status_code, caught.value.code) == (422, "RUBRIC_MISSING")


def test_analyze_empty_stt_slide_skips_llm_and_marks_every_unit_missing():
    eval_llm = SaidAllLLM()
    response = _analyze(_request(stt={6: ""}), _llms(eval_llm))
    assert 6 not in eval_llm.called_slides and sorted(eval_llm.called_slides) == [4, 7]
    empty = next(e for e in response.evaluations if e.slide_number == 6)
    units = [u for s in empty.sentences for u in s.units]
    assert units and {u.status for u in units} == {"missing"}
    assert empty.stt_text == "" and empty.scores["content_coverage"] == 0.0
    assert response.take_scores.scored_ratio == 1.0
    assert response.meta.model == MODEL  # 다른 장은 LLM 출력을 썼다


def test_analyze_meta_model_is_none_when_only_empty_stt():
    eval_llm = SaidAllLLM()
    response = _analyze(_request(slide_numbers=[6], stt={6: ""}), _llms(eval_llm))
    assert [e.slide_number for e in response.evaluations] == [6]
    assert response.meta.model is None and eval_llm.calls == 0


def test_analyze_partial_llm_failure_goes_to_failed():
    eval_llm = SaidAllLLM(failing={6: TimeoutError("STT 원문")})
    response = _analyze(llms=_llms(eval_llm))
    assert [e.slide_number for e in response.evaluations] == [4, 7]
    [failure] = response.failed
    assert (failure.slide_number, failure.error.code) == (6, "LLM_TIMEOUT")
    assert failure.error.message == ERROR_MESSAGES["LLM_TIMEOUT"]
    assert response.take_scores.scored_ratio == pytest.approx(0.667)


def test_analyze_all_failed_raises_lowest_slide_llm_code():
    failing = {4: RuntimeError("x"), 6: TimeoutError("y"), 7: TimeoutError("z")}
    with pytest.raises(CoverageError) as caught:
        _analyze(llms=_llms(SaidAllLLM(failing)))
    assert (caught.value.status_code, caught.value.code) == (502, "LLM_ERROR")  # 4번 장의 코드


def test_analyze_all_failed_prefers_llm_code_over_rubric_missing():
    request = _request(slide_numbers=[4, 6], rubrics=_rubrics_of(6))
    with pytest.raises(CoverageError) as caught:
        _analyze(request, _llms(SaidAllLLM({6: TimeoutError()})))
    assert (caught.value.status_code, caught.value.code) == (504, "LLM_TIMEOUT")


def test_analyze_llms_none_is_503():
    with pytest.raises(CoverageError) as caught:
        evaluate_take(_request(), llms=None, model=MODEL)
    assert (caught.value.status_code, caught.value.code) == (503, "LLM_UNAVAILABLE")


def test_analyze_request_validation():
    with pytest.raises(ValidationError):
        AnalyzeRequest.model_validate(_request(numbers=(4, 4)))
    with pytest.raises(ValidationError):
        AnalyzeRequest.model_validate(_request(numbers=(4,), slide_numbers=[4, 9]))
    with pytest.raises(ValidationError):
        AnalyzeRequest.model_validate(_request(numbers=()))


def test_take_scores_scored_ratio():
    evaluations = _analyze().evaluations
    assert take_scores(evaluations, [4, 6, 7, 8]).scored_ratio == 0.75
    assert take_scores(evaluations, []).scored_ratio == 0.0
    assert take_scores([], [4]).scored_ratio == 0.0


# ── /takes/rescore ───────────────────────────────────────────


def _item(item_id: str, sentence_index: int) -> SimilarItem:
    return SimilarItem(
        item_id=item_id,
        kind="word",
        script_text="좌석",
        stt_text="자석",
        script_sentence_index=sentence_index,
        script_span=(0, 2),
        stt_sentence_index=0,
        stt_span=(0, 2),
        stt_raw_span=None,
        key_point_ids=[],
        signals=[],
        rule_guess="asr_error",
    )


@pytest.fixture(scope="module")
def stored():
    """analyze 응답의 채점에 비슷한 말 R1 · R2 를 4번 장에, R1 을 6번 장에 끼운 것 (저장해 둔 채점)."""
    response = _analyze()
    evaluations = {e.slide_number: e.model_copy(deep=True) for e in response.evaluations}
    for number, ids in {4: ["R1", "R2"], 6: ["R1"]}.items():
        first = evaluations[number].sentences[0].sentence_index
        evaluations[number].similar_items = [_item(i, first) for i in ids]
    return [evaluations[n] for n in sorted(evaluations)]


def _rescore_request(evaluations, answers=(), failed=(), rubrics=None) -> dict:
    return {
        "take_id": "take-123",
        "evaluations": [e.model_dump() for e in evaluations],
        "rubrics": [r.model_dump() for r in (RUBRICS if rubrics is None else rubrics)],
        "answers": list(answers),
        "failed": list(failed),
    }


def _answer(slide, item, answer="as_script") -> dict:
    return {"slide_number": slide, "item_id": item, "answer": answer}


def test_rescore_without_answers_recomputes_take_values_only(stored):
    response = rescore_evaluation(_rescore_request(stored))
    assert [e.slide_number for e in response.evaluations] == [4, 6, 7]
    assert all(not e.applied_answers for e in response.evaluations)
    assert [(c.slide_number, c.item_id) for c in response.confirmations] == [
        (4, "R1"),
        (4, "R2"),
        (6, "R1"),
    ]
    assert response.take_scores.similar_words == 3
    assert response.meta.model is None
    assert response.meta.criteria_versions == _analyze().meta.criteria_versions
    assert rescore_evaluation(_rescore_request(stored)).model_dump() == response.model_dump()


def test_rescore_answers_reduce_confirmations(stored):
    response = rescore_evaluation(_rescore_request(stored, [_answer(4, "R1"), _answer(6, "R9")]))
    assert {e.slide_number: e.applied_answers for e in response.evaluations} == {
        4: {"R1": "as_script"},
        6: {},  # 모르는 항목의 답은 버린다
        7: {},
    }
    assert [(c.slide_number, c.item_id) for c in response.confirmations] == [(4, "R2"), (6, "R1")]
    assert response.take_scores.similar_words == 2
    assert stored[0].applied_answers == {}  # 요청으로 받은 채점은 바꾸지 않는다


def test_rescore_keeps_first_answer_when_resent_or_changed(stored):
    first = rescore_evaluation(_rescore_request(stored, [_answer(4, "R1", "as_stt")]))
    marker = "[사용자 확인]"
    reasons = [s.reason for e in first.evaluations if e.slide_number == 4 for s in e.sentences]
    assert sum(r.count(marker) for r in reasons) == 1
    # 저장된 채점(응답)에 앞 답을 다시 보내고, 다른 답도 보낸다: 처음 답이 남고 이유 문장이 겹치지 않는다
    second = rescore_evaluation(
        _rescore_request(
            first.evaluations,
            [_answer(4, "R1", "as_stt"), _answer(4, "R1", "as_script"), _answer(4, "R2")],
        )
    )
    four = next(e for e in second.evaluations if e.slide_number == 4)
    assert four.applied_answers == {"R1": "as_stt", "R2": "as_script"}
    reasons = [s.reason for s in four.sentences]
    assert sum(r.count(marker) for r in reasons) == 1
    # 같은 요청 안에서 같은 항목 답이 둘이면 처음 것
    twice = rescore_evaluation(
        _rescore_request(stored, [_answer(4, "R2", "as_script"), _answer(4, "R2", "as_stt")])
    )
    assert next(e for e in twice.evaluations if e.slide_number == 4).applied_answers == {
        "R2": "as_script"
    }


def test_rescore_scored_ratio_with_and_without_failed(stored):
    plain = rescore_evaluation(_rescore_request(stored))
    assert plain.take_scores.scored_ratio == 1.0 and plain.failed == []
    failure = {
        "slide_number": 8,
        "error": {"code": "LLM_TIMEOUT", "message": ERROR_MESSAGES["LLM_TIMEOUT"]},
    }
    with_failed = rescore_evaluation(_rescore_request(stored, failed=[failure]))
    assert with_failed.take_scores.scored_ratio == 0.75
    assert [(f.slide_number, f.error.code) for f in with_failed.failed] == [(8, "LLM_TIMEOUT")]
    # 이미 채점이 있는 장이 failed 에 남아 있으면 failed 에서 뺀다 (대상 장은 합집합)
    stale = {**failure, "slide_number": 4}
    merged = rescore_evaluation(_rescore_request(stored, failed=[stale]))
    assert merged.failed == [] and merged.take_scores.scored_ratio == 1.0


def test_analyze_then_rescore_scored_ratio_matches():
    analyzed = _analyze(_request(rubrics=_rubrics_of(4, 7)))
    assert analyzed.take_scores.scored_ratio == pytest.approx(0.667)
    again = rescore_evaluation(
        _rescore_request(
            analyzed.evaluations,
            failed=[f.model_dump() for f in analyzed.failed],
            rubrics=_rubrics_of(4, 7),
        )
    )
    assert again.take_scores.scored_ratio == analyzed.take_scores.scored_ratio
    assert again.failed == analyzed.failed


def test_rescore_rejects_missing_rubric_mismatch_and_missing_stt_text(stored):
    with pytest.raises(CoverageError) as caught:
        rescore_evaluation(_rescore_request(stored, rubrics=_rubrics_of(4, 7)))
    error = caught.value
    assert (error.status_code, error.code) == (422, "RUBRIC_MISSING")
    assert error.message == "6번 장의 평가 기준이 없습니다."

    changed = stored[0].model_copy(update={"rubric_id": "other"})
    with pytest.raises(CoverageError) as caught:
        rescore_evaluation(_rescore_request([changed, *stored[1:]]))
    error = caught.value
    assert (error.status_code, error.code) == (422, "RUBRIC_MISMATCH")
    assert error.message == "4번 장의 채점과 평가 기준이 맞지 않습니다. 다시 채점해야 합니다."

    old = stored[1].model_dump()
    del old["stt_text"]  # 예전 모양
    request = _rescore_request(stored)
    request["evaluations"][1] = old
    with pytest.raises(CoverageError) as caught:
        rescore_evaluation(request)
    error = caught.value
    assert (error.status_code, error.code) == (422, "STT_TEXT_MISSING")
    assert error.message == "6번 장의 채점에 STT 텍스트가 없습니다. 다시 채점해야 합니다."


def test_rescore_checks_slides_in_order_and_empty_stt_text_is_fine(stored):
    # 4번 장은 rubric_id 가 다르고 6번 장은 평가 기준이 없다 → 번호가 작은 4번 장의 문제가 먼저다
    changed = stored[0].model_copy(update={"rubric_id": "other"})
    with pytest.raises(CoverageError) as caught:
        rescore_evaluation(_rescore_request([stored[1], changed], rubrics=_rubrics_of(4, 7)))
    assert caught.value.code == "RUBRIC_MISMATCH"
    skipped = _analyze(_request(slide_numbers=[6], stt={6: ""}), _llms()).evaluations  # 넘어간 장
    result = rescore_evaluation(_rescore_request(skipped))
    assert result.evaluations[0].stt_text == "" and result.take_scores.scored_ratio == 1.0


def test_confirmations_lists_items_without_answers(stored):
    answered = stored[0].model_copy(update={"applied_answers": {"R2": "as_stt"}})
    result = confirmations([stored[1], answered])  # 입력 순서와 상관없이 장 번호 순
    assert [(c.slide_number, c.item_id, c.script_text, c.stt_text) for c in result] == [
        (4, "R1", "좌석", "자석"),
        (6, "R1", "좌석", "자석"),
    ]


def test_rescore_request_validation(stored):
    with pytest.raises(ValidationError):
        RescoreRequest.model_validate(_rescore_request([stored[0], stored[0]]))
