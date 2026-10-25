"""최상위 진입 함수 `core.evaluate_take` (`/takes/analyze`) · `core.rescore_evaluation` (`/takes/rescore`)."""

import re

import pytest
from pydantic import ValidationError

from script_coverage.core import (
    CoverageError,
    TakeLLMs,
    confirmations,
    evaluate_take,
    rescore_evaluation,
    take_scores,
)
from script_coverage.schemas import AnalyzeRequest, RescoreRequest, SlideFailure
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

from .fakes import FakeLLM, KeepFirstVerifier, deck_rubrics
from .shared.slides import SLIDES

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


RUBRICS = deck_rubrics(NUMBERS)


def _llms(eval_llm=None, verifier=None) -> TakeLLMs:
    return TakeLLMs(eval=eval_llm or SaidAllLLM(), verifier=verifier or KeepFirstVerifier())


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


@pytest.mark.parametrize("stt", ["", "음 어"])  # 간투사뿐인 STT 도 정규화하면 빈 것이다
def test_analyze_empty_stt_has_no_matched_facts(stt):
    """이름 · 수치가 많은 장도 STT 가 비면 어떤 수치도 맞게 말한 것으로 세지 않고, LLM 도 부르지 않는다."""
    eval_llm, verifier = SaidAllLLM(), FakeLLM(VerifierResult(judgments=[]))
    response = _analyze(
        _request(slide_numbers=[4, 6], stt={4: stt, 6: stt}), _llms(eval_llm, verifier)
    )
    assert (eval_llm.calls, verifier.calls) == (0, 0) and response.meta.model is None
    assert [e.slide_number for e in response.evaluations] == [4, 6]
    for e in response.evaluations:
        assert e.stt_text == ""
        assert e.critical_facts and {c.status for c in e.critical_facts} == {"missing"}
        assert e.scores["critical_fact_accuracy"] == 0.0  # 수치가 있는데 하나도 말하지 않았다
        assert e.scores["_weights"]["facts"] > 0
        assert e.scores["content_coverage"] == 0.0 and e.scores["script_fidelity"] == 0.0
        assert not e.similar_items
    assert response.take_scores.scored_ratio == 1.0


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


def test_analyze_empty_slide_numbers_is_empty_200_even_without_rubrics():
    for rubrics in ([], RUBRICS):
        response = _analyze(_request(slide_numbers=[], rubrics=rubrics))
        assert response.evaluations == [] and response.failed == []
        assert response.take_scores.scored_ratio == 0.0 and response.meta.model is None


def test_analyze_verifier_failure_fails_only_that_slide():
    """충돌이 있는 장은 교차 검증을 부른다: 검증이 실패하면 그 장만 failed, 나머지 장은 채점된다."""

    class ConflictLLM(SaidAllLLM):
        """6번 장은 말한 내용을 전부 missing 으로 판정한다 (규칙과 어긋나 충돌이 생긴다)."""

        def invoke(self, messages):
            result = super().invoke(messages)
            if self.called_slides[-1] == 6:
                for s in result.sentences:
                    for u in s.units:
                        u.status = "missing"
            return result

    class SlideVerifier:
        """6번 장의 교차 검증만 시간 초과가 난다."""

        def __init__(self):
            self.calls = 0

        def invoke(self, messages):
            self.calls += 1
            if (
                "1,860명" in messages[1][1]
            ):  # 검증 메시지에는 장 번호가 없어 6번 장 대본으로 알아본다
                raise TimeoutError("STT 원문")
            return KeepFirstVerifier().invoke(messages)

    verifier = SlideVerifier()
    response = _analyze(llms=_llms(ConflictLLM(), verifier))
    assert verifier.calls >= 1
    [failure] = response.failed
    assert failure.slide_number == 6 and failure.error.code == "LLM_TIMEOUT"
    assert failure.error.message == ERROR_MESSAGES["LLM_TIMEOUT"]
    assert [e.slide_number for e in response.evaluations] == [4, 7]
    assert response.take_scores.scored_ratio == pytest.approx(0.667)


class _MissingOn6(SaidAllLLM):
    """6번 장은 말한 내용을 전부 missing 으로 판정한다 (규칙과 어긋나 교차 검증 대상이 생긴다)."""

    def invoke(self, messages):
        result = super().invoke(messages)
        if self.called_slides[-1] == 6:
            for s in result.sentences:
                for u in s.units:
                    u.status = "missing"
        return result


class _BrokenVerifier(KeepFirstVerifier):
    """완전한 검증 출력을 만든 뒤, 6번 장이면 `break_it` 으로 일부를 망가뜨린다."""

    def __init__(self, break_it):
        super().__init__()
        self.break_it = break_it

    def invoke(self, messages):
        result = super().invoke(messages)
        if "1,860명" in messages[1][1]:  # 검증 메시지에는 장 번호가 없어 6번 장 대본으로 알아본다
            self.break_it(result.judgments)
        return result


def _drop_sentence(js):
    js.pop()


def _drop_unit(js):
    js[0].units.pop()


def _repeat_sentence(js):
    js.append(js[0].model_copy(deep=True))


def _repeat_unit(js):
    js[0].units.append(js[0].units[0].model_copy())


@pytest.mark.parametrize("break_it", [_drop_sentence, _drop_unit, _repeat_sentence, _repeat_unit])
def test_analyze_incomplete_verification_fails_that_slide(break_it):
    """교차 검증 출력이 대상 문장 · 전달 단위를 빠뜨리거나 겹치면 그 장은 1차 판정으로 내지 않는다."""
    verifier = _BrokenVerifier(break_it)
    response = _analyze(llms=_llms(_MissingOn6(), verifier))
    [failure] = response.failed
    assert failure.slide_number == 6 and failure.error.code == "LLM_INVALID_OUTPUT"
    assert [e.slide_number for e in response.evaluations] == [4, 7]


class _MemoryCache:
    def __init__(self):
        self.rows: dict[tuple, str] = {}

    def get(self, kind, key, config_hash):
        return self.rows.get((kind, key, config_hash))

    def put(self, kind, key, config_hash, output_json):
        self.rows[(kind, key, config_hash)] = output_json


def test_incomplete_verification_is_not_cached_and_a_retry_recovers():
    """잘못된 검증 출력은 캐시에 남지 않고, 캐시에 있던 것도 다시 부른다 — 다시 채점하면 회복된다."""
    cache = _MemoryCache()
    broken = evaluate_take(
        _request(), llms=_llms(_MissingOn6(), _BrokenVerifier(_drop_unit)), model=MODEL, cache=cache
    )
    assert [f.slide_number for f in broken.failed] == [6]
    good = KeepFirstVerifier()
    again = evaluate_take(_request(), llms=_llms(_MissingOn6(), good), model=MODEL, cache=cache)
    assert again.failed == [] and good.calls >= 1
    # 예전에 캐시에 들어간 잘못된 출력도 쓰지 않고 다시 부른다
    for key in [k for k in cache.rows if k[0] == "stt.verifier"]:
        cache.rows[key] = '{"judgments": []}'
    good = KeepFirstVerifier()
    third = evaluate_take(_request(), llms=_llms(_MissingOn6(), good), model=MODEL, cache=cache)
    assert third.failed == [] and good.calls >= 1


def test_analyze_complete_verification_scores_the_slide():
    response = _analyze(llms=_llms(_MissingOn6(), KeepFirstVerifier()))
    assert response.failed == []
    [six] = [e for e in response.evaluations if e.slide_number == 6]
    assert six.verification["required"] is True
    assert all(s.verified for s in six.sentences if s.conflicts)


def test_rescore_rejects_failed_slide_number_below_one():
    with pytest.raises(ValidationError):
        SlideFailure.model_validate(
            {"slide_number": 0, "error": {"code": "LLM_ERROR", "message": "m"}}
        )


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
    span = (0, 2) if item_id == "R1" else (30, 32)  # 자리가 겹치지 않게
    return SimilarItem(
        item_id=item_id,
        kind="word",
        script_text="좌석",
        stt_text="자석",
        script_sentence_index=sentence_index,
        script_span=span,
        stt_sentence_index=0,
        stt_span=span,
        stt_raw_span=None,
        key_point_ids=[],
        signals=[],
        rule_guess="asr_error",
    )


@pytest.fixture(scope="module")
def stored():
    """analyze 응답의 채점에 비슷한 말 R1 · R2 를 4번 장에, R1 을 6번 장에 끼운 것 (저장해 둔 채점)."""
    # 4번 장은 첫 단어를 다르게 말했다: 비슷한 말 자리(0~2)를 대본 충실도에 넣느냐에 따라 값이 달라진다
    response = _analyze(_request(stt={4: "자석" + _script(4)[2:]}))
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


_KEPT = ["scores", "sentences", "key_points", "critical_facts", "applied_answers"]


def _kept(evaluations) -> list[dict]:
    return [e.model_dump(include=set(_KEPT)) for e in evaluations]


def test_rescore_without_answers_preserves_analyzed_evaluations():
    """analyze 응답을 답 없이 다시 계산해도 장별 채점 내용이 그대로다 (Take 값만 다시 계산)."""
    analyzed = _analyze()
    again = rescore_evaluation(_rescore_request(analyzed.evaluations))
    assert _kept(again.evaluations) == _kept(analyzed.evaluations)
    assert again.take_scores == analyzed.take_scores


def test_rescore_without_answers_preserves_cumulative_answers(stored):
    first = rescore_evaluation(
        _rescore_request(stored, [_answer(4, "R1", "as_stt"), _answer(6, "R1")])
    )
    again = rescore_evaluation(_rescore_request(first.evaluations))
    assert _kept(again.evaluations) == _kept(first.evaluations)
    assert again.take_scores == first.take_scores
    assert again.confirmations == first.confirmations


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
    # 처음 답(as_stt)의 효과가 새 답이 와도 그대로다: 그 문장은 계속 contradicted 이고,
    # 대본 충실도(as_stt 자리는 넣고 계산)와 점수가 첫 다시 계산과 같다
    first_four = next(e for e in first.evaluations if e.slide_number == 4)
    index = stored[0].sentences[0].sentence_index
    assert next(s for s in four.sentences if s.sentence_index == index).status == "contradicted"
    assert four.fidelity == first_four.fidelity
    keys = ["content_coverage", "critical_fact_accuracy", "script_fidelity"]
    assert {k: four.scores[k] for k in keys} == {k: first_four.scores[k] for k in keys}
    assert four.scores["similar_words"] == first_four.scores["similar_words"] - 1  # R2 에 답함
    assert (
        four.fidelity != stored[0].fidelity
    )  # 시험이 as_stt 효과에 민감한지    # 같은 요청 안에서 같은 항목 답이 둘이면 처음 것
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


def test_rescore_failed_duplicates_keep_first(stored):
    first = {"slide_number": 8, "error": {"code": "LLM_TIMEOUT", "message": "첫 번째"}}
    second = {"slide_number": 8, "error": {"code": "LLM_ERROR", "message": "두 번째"}}
    response = rescore_evaluation(_rescore_request(stored, failed=[first, second]))
    assert [(f.slide_number, f.error.message) for f in response.failed] == [(8, "첫 번째")]
    assert response.take_scores.scored_ratio == 0.75


def test_duplicate_rubric_slide_numbers_are_rejected(stored):
    twice = [RUBRICS[0], RUBRICS[0]]
    with pytest.raises(ValidationError):
        AnalyzeRequest.model_validate(_request(rubrics=twice))
    with pytest.raises(ValidationError):
        RescoreRequest.model_validate(_rescore_request(stored, rubrics=twice))


def test_rescore_request_validation(stored):
    with pytest.raises(ValidationError):
        RescoreRequest.model_validate(_rescore_request([stored[0], stored[0]]))
