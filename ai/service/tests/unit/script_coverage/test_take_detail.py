"""Take 상세 · 실패 장 간이 값 · STT 완전성 — 진입 함수(`evaluate_take` · `rescore_evaluation`)를 가짜 LLM 으로 돌린다."""

import pytest

from pitch_coach_ai.features.script_coverage.core import CoverageError, rescore_evaluation

from .test_take_core import (
    NUMBERS,
    RUBRICS,
    SaidAllLLM,
    _analyze,
    _item,
    _llms,
    _request,
    _rescore_request,
    _rubrics_of,
    _script,
)


def _timeout_on(*numbers):
    return _llms(SaidAllLLM({n: TimeoutError("x") for n in numbers}))


def _with_timing(request, per_slide):
    for s in request["slides"]:
        s.update(per_slide.get(s["slide_number"], {}))
    return request


def test_one_failed_slide_has_fallback_and_take_detail_is_provisional():
    response = _analyze(llms=_timeout_on(6))
    [failure] = response.failed
    fb = failure.fallback
    assert fb.method == "lexical" and fb.reason_codes == ["LLM_TIMEOUT"]
    assert fb.delivery_band == "mostly" and fb.stt_integrity == "unknown"
    d = response.detail
    assert d.scope.model_dump() == {
        "target_slides": 3,
        "scored_slides": 2,
        "estimated_slides": 1,
        "provisional_slides": 0,
        "excluded": [{"slide_number": 6, "reason_code": "LLM_TIMEOUT"}],
    }
    assert (d.delivery.state, d.delivery.reason_codes) == ("provisional", ["SLIDES_EXCLUDED"])
    assert d.delivery.excluded == len(RUBRICS[1].key_points)
    assert [(s.slide_number, s.state) for s in d.slides] == [
        (4, "scored"),
        (6, "estimated"),
        (7, "scored"),
    ]
    est = d.slides[1]
    assert est.delivery is None and est.delivery_band == "mostly" and est.speech_mix is None
    assert est.reason_codes == ["LLM_TIMEOUT"] and est.rubric_quality == "full"
    assert d.slides[0].delivery.value is not None and d.slides[0].speech_mix is not None
    # 기존 값은 그대로: take_scores 에는 새 필드가 없다
    assert set(response.take_scores.model_dump()) == {
        "content_coverage",
        "critical_fact_accuracy",
        "script_fidelity",
        "similar_words",
        "similar_numbers",
        "scored_ratio",
    }


def test_all_failed_with_fallback_is_200():
    response = _analyze(llms=_timeout_on(*NUMBERS))
    assert response.evaluations == [] and len(response.failed) == 3
    assert all(f.fallback for f in response.failed)
    d = response.detail
    assert (d.scope.scored_slides, d.scope.estimated_slides) == (0, 3)
    for m in (
        d.delivery,
        d.contradiction,
        d.facts.accuracy,
        d.fidelity.recall,
        d.fidelity.precision,
    ):
        assert (m.value, m.state, m.reason_codes) == (None, "failed", ["SLIDES_EXCLUDED"])
    assert d.status_shares is None and d.facts.accuracy_range is None and d.speech_mix.display == []
    assert d.zero_kind is None and d.critical_gaps == []
    assert (
        response.take_scores.content_coverage is None and response.take_scores.scored_ratio == 0.0
    )


def test_empty_stt_with_incomplete_integrity_is_failed_not_scored():
    request = _with_timing(
        _request(stt={6: ""}),
        {6: {"duration_ms": 60000, "stt_gap_ms": 30000, "stt_unobserved_ms": 0}},
    )
    eval_llm = SaidAllLLM()
    response = _analyze(request, _llms(eval_llm))
    [failure] = response.failed
    assert (failure.slide_number, failure.error.code, failure.fallback) == (
        6,
        "STT_INCOMPLETE",
        None,
    )
    assert failure.error.message == "6번 장: 음성 인식이 끊겨 이 장을 채점하지 않았습니다."
    assert [e.slide_number for e in response.evaluations] == [4, 7]
    assert response.detail.slides[1].state == "failed"
    assert response.detail.slides[1].stt_integrity == "incomplete"


def test_all_slides_stt_incomplete_is_200_with_reasons():
    """대상 장이 모두 STT 끊김이어도 422 대신 200 — BE 는 에러 본문을 읽지 않아 이유를 보일 수 없다."""
    timing = {"duration_ms": 60000, "stt_gap_ms": 30000, "stt_unobserved_ms": 0}
    request = _with_timing(_request(slide_numbers=[6], stt={6: ""}), {6: timing})
    response = _analyze(request)
    assert response.evaluations == []
    [failure] = response.failed
    assert (failure.slide_number, failure.error.code, failure.fallback) == (
        6,
        "STT_INCOMPLETE",
        None,
    )
    scores = response.take_scores
    assert (scores.content_coverage, scores.critical_fact_accuracy, scores.script_fidelity) == (
        None,
        None,
        None,
    )
    assert scores.scored_ratio == 0
    assert response.confirmations == []
    assert response.meta.model is None
    assert [(x.slide_number, x.reason_code) for x in response.detail.scope.excluded] == [
        (6, "STT_INCOMPLETE")
    ]
    assert response.detail.slides[0].state == "failed"


def test_incomplete_with_stt_is_scored_provisional():
    timing = {"duration_ms": 60000, "stt_gap_ms": 30000, "stt_unobserved_ms": 0}
    response = _analyze(_with_timing(_request(), {6: timing}))
    six = next(e for e in response.evaluations if e.slide_number == 6)
    assert six.stt_integrity == "incomplete"
    assert (six.detail.state, six.detail.reason_codes) == ("provisional", ["STT_INCOMPLETE"])
    d = response.detail
    assert d.scope.provisional_slides == 1
    assert (d.delivery.state, d.delivery.reason_codes) == ("provisional", ["STT_INCOMPLETE"])


def test_empty_stt_unknown_is_skipped_unverified_and_complete_is_plain():
    response = _analyze(_request(slide_numbers=[6], stt={6: ""}))
    d = response.evaluations[0].detail
    assert (d.zero_kind, d.state, d.reason_codes) == (
        "slide_skipped",
        "provisional",
        ["STT_UNVERIFIED"],
    )
    assert d.delivery.value == 0.0 and d.stt_integrity == "unknown"
    timing = {"duration_ms": 60000, "stt_gap_ms": 0, "stt_unobserved_ms": 0}
    done = _analyze(_with_timing(_request(slide_numbers=[6], stt={6: ""}), {6: timing}))
    d = done.evaluations[0].detail
    assert (d.zero_kind, d.state, d.stt_integrity) == ("slide_skipped", "ok", "complete")
    assert done.detail.zero_kind == "slide_skipped"


def test_missing_rubric_with_script_gets_no_rubric_fallback():
    request = _request(rubrics=_rubrics_of(4, 7))
    for s in request["slides"]:
        s["script"] = _script(s["slide_number"])
    response = _analyze(request)
    [failure] = response.failed
    assert failure.error.code == "RUBRIC_MISSING"
    assert failure.fallback.method == "lexical_no_rubric" and failure.fallback.speech_mix is None
    assert failure.fallback.delivery_band == "mostly"
    assert (
        response.detail.slides[1].state == "estimated"
        and response.detail.slides[1].rubric_quality is None
    )


def test_missing_rubric_without_script_has_no_fallback():
    response = _analyze(_request(rubrics=_rubrics_of(4, 7)))
    assert response.failed[0].fallback is None and response.detail.slides[1].state == "failed"


def test_empty_rubrics_with_script_is_200_with_fallbacks():
    request = _request(rubrics=[])
    for s in request["slides"]:
        s["script"] = _script(s["slide_number"])
    response = _analyze(request)
    assert response.evaluations == [] and len(response.failed) == 3
    assert {f.fallback.method for f in response.failed} == {"lexical_no_rubric"}
    assert response.detail.scope.estimated_slides == 3
    # 대본 글이 하나도 없으면 지금처럼 422
    with pytest.raises(CoverageError):
        _analyze(_request(rubrics=[]))


def test_empty_targets_detail_is_not_applicable():
    d = _analyze(_request(slide_numbers=[])).detail
    assert d.scope.target_slides == 0 and d.slides == []
    assert d.delivery.state == "not_applicable" and d.speech_mix.state == "not_applicable"


def test_take_critical_gaps_are_sorted_with_slide_number():
    response = _analyze(_request(stt={4: "", 7: ""}))
    gaps = response.detail.critical_gaps
    assert gaps and all(g.slide_number in (4, 7) for g in gaps)
    keys = [({"critical": 0, "high": 1}[g.importance], g.slide_number) for g in gaps]
    assert keys == sorted(keys) and len(gaps) <= 10
    assert response.detail.zero_kind is None  # 6번 장은 STT 가 있어 모두 말함


def test_rescore_without_answers_gives_the_same_detail():
    analyzed = _analyze(_request(rubrics=_rubrics_of(4, 7), stt={7: ""}), _timeout_on(4))
    again = rescore_evaluation(
        _rescore_request(
            analyzed.evaluations,
            failed=[f.model_dump() for f in analyzed.failed],
            rubrics=_rubrics_of(4, 7),
        )
    )
    assert again.detail.model_dump() == analyzed.detail.model_dump()
    assert [e.detail.model_dump() for e in again.evaluations] == [
        e.detail.model_dump() for e in analyzed.evaluations
    ]
    assert [f.fallback for f in again.failed] == [f.fallback for f in analyzed.failed]


def test_rescore_as_stt_answer_changes_delivery_and_contradiction():
    response = _analyze(_request(slide_numbers=[4], stt={4: "자석" + _script(4)[2:]}))
    stored = response.evaluations[0].model_copy(deep=True)
    stored.similar_items = [_item("R1", stored.sentences[0].sentence_index)]
    base = rescore_evaluation(_rescore_request([stored], rubrics=_rubrics_of(4)))
    answer = {"slide_number": 4, "item_id": "R1", "answer": "as_stt"}
    after = rescore_evaluation(_rescore_request([stored], [answer], rubrics=_rubrics_of(4)))
    b, a = base.evaluations[0].detail, after.evaluations[0].detail
    assert a.contradiction.value > b.contradiction.value
    assert a.delivery.value < b.delivery.value
    assert after.detail.contradiction.value == a.contradiction.value


def test_old_evaluation_without_detail_is_rescored_with_detail():
    analyzed = _analyze()
    old = [
        e.model_copy(update={"detail": None, "offscript": None, "stt_integrity": None})
        for e in analyzed.evaluations
    ]
    response = rescore_evaluation(_rescore_request(old))
    assert all(e.detail is not None and e.offscript is not None for e in response.evaluations)
    assert response.evaluations[0].stt_integrity == "unknown"
    assert [e.detail.delivery for e in response.evaluations] == [
        e.detail.delivery for e in analyzed.evaluations
    ]


def test_response_json_shape_matches_the_contract():
    body = _analyze(llms=_timeout_on(*NUMBERS)).model_dump(mode="json")
    assert list(body) == [
        "take_id",
        "evaluations",
        "take_scores",
        "confirmations",
        "failed",
        "detail",
        "meta",
    ]
    assert list(body["detail"]) == [
        "version",
        "delivery",
        "zero_kind",
        "contradiction",
        "status_shares",
        "key_points",
        "critical_gaps",
        "legacy",
        "facts",
        "expression",
        "fidelity",
        "speech_mix",
        "scope",
        "slides",
    ]
    assert body["detail"]["delivery"] == {
        "value": None,
        "state": "failed",
        "reason_codes": ["SLIDES_EXCLUDED"],
        "evaluated": 0,
        "excluded": sum(len(r.key_points) for r in RUBRICS),
    }
    assert body["detail"]["legacy"] == {"content_coverage_raw": None}
    assert body["detail"]["key_points"]["critical"] == {
        "covered": 0,
        "partial": 0,
        "missing": 0,
        "contradicted": 0,
    }
    assert list(body["failed"][0]["fallback"]) == [
        "version",
        "method",
        "delivery_band",
        "lexical_coverage",
        "facts",
        "fact_accuracy",
        "script_recall",
        "speech_mix",
        "stt_integrity",
        "reason_codes",
    ]
    assert list(body["detail"]["slides"][0]) == [
        "slide_number",
        "state",
        "reason_codes",
        "stt_integrity",
        "rubric_quality",
        "delivery",
        "zero_kind",
        "contradiction",
        "status_shares",
        "critical_gap_count",
        "delivery_band",
        "speech_mix",
    ]
    scored = _analyze().model_dump(mode="json")["evaluations"][0]
    assert list(scored["detail"]) == [
        "version",
        "state",
        "reason_codes",
        "stt_integrity",
        "rubric_quality",
        "delivery",
        "zero_kind",
        "contradiction",
        "status_shares",
        "key_points",
        "critical_gaps",
        "legacy",
        "facts",
        "expression",
        "fidelity",
        "speech_mix",
    ]
    assert scored["stt_integrity"] == "unknown" and scored["detail"]["legacy"]["zero_kind"] is None
