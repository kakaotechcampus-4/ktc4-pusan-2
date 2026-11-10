"""결과 상세 검증 반영(계약 · rescore · 상세 계산 · 흐름 · 말의 구성)의 시험.

앞쪽은 항목별 동작, 뒤쪽은 빈틈을 메우는 흐름 시험(`said_in_slides` · rescore 일치 · 모든 장 실패 · 일부 대상)이다.
"""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from pitch_coach_ai.features.script_coverage import core as core_module
from pitch_coach_ai.features.script_coverage.config import SCORE_WEIGHT
from pitch_coach_ai.features.script_coverage.core import (
    CoverageError,
    rescore_evaluation,
)
from pitch_coach_ai.features.script_coverage.schemas import TakeCriticalGap
from pitch_coach_ai.features.script_coverage.shared.text import normalize_script
from pitch_coach_ai.features.script_coverage.stt_evaluation import core as stt_core
from pitch_coach_ai.features.script_coverage.stt_evaluation.align import (
    positioned_tokens,
    without_spans,
)
from pitch_coach_ai.features.script_coverage.stt_evaluation.detail import (
    rate,
    slide_detail,
)
from pitch_coach_ai.features.script_coverage.stt_evaluation.fallback import (
    evidence_skip_alignments,
    lexical_fallback,
)
from pitch_coach_ai.features.script_coverage.stt_evaluation.schemas import (
    Fidelity,
    Metric,
)
from pitch_coach_ai.features.script_coverage.stt_evaluation.speech_mix import (
    _is_procedural,
    said_in_other_slides,
)
from pitch_coach_ai.features.script_coverage.take_detail import take_detail

from .stt_evaluation.test_speech_mix import OTHER, rubric, run
from .test_take_core import (
    NUMBERS,
    RUBRICS,
    _analyze,
    _item,
    _request,
    _rescore_request,
    _rubrics_of,
    _script,
)
from .test_take_detail import _timeout_on, _with_timing

INCOMPLETE = {"duration_ms": 60000, "stt_gap_ms": 30000, "stt_unobserved_ms": 0}
ASIDE = "어제 본 드라마가 갑자기 생각나네요 주인공이 정말 멋있었습니다."
BY_SLIDE = {r.meta.slide_number: r for r in RUBRICS}


# ── 1. 계약 · rescore ────────────────────────────────────────


def test_rescore_keeps_failed_slide_integrity_from_the_failure():
    request = _with_timing(
        _request(rubrics=_rubrics_of(4, 6), stt={6: ""}), {6: INCOMPLETE}
    )  # 6번: 끊겨 빈 장, 7번: 평가 기준 없음(간이 값 없음)
    analyzed = _analyze(request)
    assert {f.slide_number: f.error.code for f in analyzed.failed} == {
        6: "STT_INCOMPLETE",
        7: "RUBRIC_MISSING",
    }
    again = rescore_evaluation(
        _rescore_request(
            analyzed.evaluations,
            failed=[f.model_dump() for f in analyzed.failed],
            rubrics=_rubrics_of(4, 6),
        )
    )
    integrity = {s.slide_number: s.stt_integrity for s in again.detail.slides}
    assert integrity == {4: "unknown", 6: "incomplete", 7: "unknown"}
    assert again.detail.model_dump() == analyzed.detail.model_dump()


def test_rescore_keeps_failed_slide_integrity_from_the_fallback():
    analyzed = _analyze(_request(), _timeout_on(6))
    [failure] = analyzed.failed
    failure.fallback.stt_integrity = "complete"
    again = rescore_evaluation(
        _rescore_request(
            analyzed.evaluations, failed=[failure.model_dump()], rubrics=_rubrics_of(4, 7)
        )
    )
    assert {s.slide_number: s.stt_integrity for s in again.detail.slides}[6] == "complete"


def test_take_critical_gap_requires_slide_number():
    with pytest.raises(ValidationError):
        TakeCriticalGap(key_point_id="KP1", content="c", importance="critical", status="missing")
    gaps = _analyze(_request(stt={4: ""})).detail.critical_gaps
    assert gaps and all(isinstance(g.slide_number, int) for g in gaps)
    spec = json.loads(
        (Path(core_module.__file__).parents[4] / "contracts/openapi.json").read_text(
            encoding="utf-8"
        )
    )
    take_gap = spec["components"]["schemas"]["TakeCriticalGap"]
    assert "slide_number" in take_gap["required"]


# ── 2. 상세 계산 ─────────────────────────────────────────────


def _partial_take(facts=None, fidelity_empty=False):
    """6번 장만 실패(빠짐)한 채점의 Take 상세를 만든다. 채점된 장의 수치 · 대본 재현을 비워 볼 수 있다."""
    response = _analyze(_request(), _timeout_on(6))
    evs = [e.model_copy(deep=True) for e in response.evaluations]
    for e in evs:
        if facts == "none":
            e.critical_facts = []
        elif facts == "pending":
            for c in e.critical_facts:
                c.status = "sound_alike"
        if fidelity_empty:
            empty = Metric(value=None, state="not_applicable")
            e.detail.fidelity = Fidelity(recall=empty, precision=empty.model_copy())
    return evs, response.failed


def test_values_missing_with_excluded_slides_are_failed_not_not_applicable():
    assert BY_SLIDE[6].critical_facts  # 빠진 6번 장에 수치가 있다
    evs, failed = _partial_take(facts="none", fidelity_empty=True)
    d = take_detail(evs, failed, [4, 6, 7], BY_SLIDE, {})
    assert (d.facts.accuracy.value, d.facts.accuracy.state, d.facts.accuracy.reason_codes) == (
        None,
        "failed",
        ["SLIDES_EXCLUDED"],
    )
    assert d.facts.accuracy.excluded == len(BY_SLIDE[6].critical_facts)
    for m in (d.fidelity.recall, d.fidelity.precision):
        assert (m.value, m.state, m.reason_codes) == (None, "failed", ["SLIDES_EXCLUDED"])


def test_all_facts_pending_with_excluded_slides_adds_the_reason():
    evs, failed = _partial_take(facts="pending")
    d = take_detail(evs, failed, [4, 6, 7], BY_SLIDE, {})
    assert (d.facts.accuracy.state, d.facts.accuracy.reason_codes) == (
        "pending",
        ["ALL_FACTS_PENDING", "SLIDES_EXCLUDED"],
    )


def test_unknown_rubric_of_excluded_slide_may_have_units():
    evs, failed = _partial_take(facts="none", fidelity_empty=True)
    d = take_detail(evs, failed, [4, 6, 7], {4: BY_SLIDE[4], 7: BY_SLIDE[7]}, {})
    assert d.facts.accuracy.state == "failed"
    assert d.fidelity.recall.state == "failed"


def test_without_excluded_slides_values_stay_not_applicable():
    evs, failed = _partial_take(facts="none", fidelity_empty=True)
    d = take_detail(evs, [], [4, 7], BY_SLIDE, {})
    assert (d.facts.accuracy.state, d.facts.accuracy.reason_codes) == (
        "not_applicable",
        ["NO_FACTS"],
    )
    assert d.fidelity.recall.state == "not_applicable"


def test_excluded_slide_without_facts_keeps_no_facts():
    evs, failed = _partial_take(facts="none")
    rubrics = dict(BY_SLIDE)
    rubrics[6] = rubrics[6].model_copy(update={"critical_facts": []})
    d = take_detail(evs, failed, [4, 6, 7], rubrics, {})
    assert d.facts.accuracy.state == "not_applicable"
    # 채점된 장 수치가 모두 대기여도 빠진 장에 수치가 없으면 SLIDES_EXCLUDED 는 붙지 않는다
    evs, failed = _partial_take(facts="pending")
    d = take_detail(evs, failed, [4, 6, 7], rubrics, {})
    assert d.facts.accuracy.state == "pending"
    assert d.facts.accuracy.reason_codes == ["ALL_FACTS_PENDING"]


def test_fidelity_precision_evaluated_excludes_similar_item_spans():
    # 첫 단어 "예측"을 비슷한 소리 "예축"으로 말해 처음 채점에서 비슷한 말 항목(R1)이 실제로 생긴다
    stt = {4: _script(4).replace("예측", "예축", 1)}
    first = _analyze(_request(slide_numbers=[4], stt=stt)).evaluations[0]
    assert [(i.item_id, i.stt_span) for i in first.similar_items] == [("R1", (0, 2))]
    tokens = positioned_tokens(normalize_script(first.stt_text))
    held = len(without_spans(tokens, [(0, 2)]))
    assert held < len(tokens)
    assert first.detail.fidelity.precision.evaluated == held  # 처음 채점은 모두 뺀다
    rubrics = _rubrics_of(4)

    def after(answer):
        answers = [{"slide_number": 4, "item_id": "R1", "answer": answer}]
        request = _rescore_request([first.model_copy(deep=True)], answers, rubrics=rubrics)
        return rescore_evaluation(request)

    base = rescore_evaluation(_rescore_request([first.model_copy(deep=True)], rubrics=rubrics))
    assert base.evaluations[0].detail.fidelity.precision.evaluated == held
    assert base.detail.fidelity.precision.evaluated == held  # Take 가중도 이 값
    assert after("as_script").evaluations[0].detail.fidelity.precision.evaluated == held
    assert after("as_stt").evaluations[0].detail.fidelity.precision.evaluated == len(tokens)


def _many_results(statuses: dict[str, int]):
    """같은 장 채점에 판정 개수만 다르게 둔 핵심 내용 목록 (모두 normal 이라 가중치 1)."""
    e = _analyze(_request(slide_numbers=[4])).evaluations[0].model_copy(deep=True)
    base = e.key_points[0]
    e.key_points = [
        base.model_copy(update={"importance": "normal", "status": status})
        for status, count in statuses.items()
        for _ in range(count)
    ]
    return e


def test_legacy_zero_kind_uses_the_unrounded_value():
    e = _many_results({"contradicted": 1001, "covered": 500})  # 원값 -0.5/1501 → 반올림하면 0
    d = slide_detail(e, RUBRICS[0], stt_integrity="complete", speech_mix=e.detail.speech_mix)
    assert d.legacy.zero_kind == "clamped_negative"
    assert d.legacy.content_coverage_raw == 0.0
    assert d.delivery.value == 0.333


def test_tiny_delivery_rate_is_never_rounded_to_zero():
    assert rate(0.5, 1101) == 0.001 and rate(0, 10) == 0.0 and rate(1, 3) == 0.333
    e = _many_results({"partial": 1, "missing": 1100})
    mix = e.detail.speech_mix
    d = slide_detail(e, RUBRICS[0], stt_integrity="complete", speech_mix=mix)
    assert (d.delivery.value, d.zero_kind, d.contradiction.value) == (0.001, None, 0.0)
    e.detail = d
    take = take_detail([e], [], [4], BY_SLIDE, {})
    assert (take.delivery.value, take.zero_kind) == (0.001, None)


def test_tiny_contradiction_rate_is_never_rounded_to_zero():
    e = _many_results({"contradicted": 1, "missing": 1100})
    d = slide_detail(e, RUBRICS[0], stt_integrity="complete", speech_mix=e.detail.speech_mix)
    assert (d.contradiction.value, d.delivery.value) == (0.001, 0.0)
    assert d.zero_kind == "missing_or_contradicted"
    e.detail = d
    take = take_detail([e], [], [4], BY_SLIDE, {})
    assert (take.contradiction.value, take.delivery.value) == (0.001, 0.0)
    assert take.zero_kind == "missing_or_contradicted"


# ── 3. 흐름 ─────────────────────────────────────────────────


def test_empty_rubrics_without_script_with_stt_incomplete_is_200():
    """평가 기준 · 대본 글이 없어도 STT 가 끊겨 뺀 장이 있으면 오류 대신 장마다 이유를 담은 200 이다."""
    request = _with_timing(_request(rubrics=[], stt={4: ""}), {4: INCOMPLETE})
    response = _analyze(request)
    assert response.evaluations == []
    assert [(f.slide_number, f.error.code, f.fallback) for f in response.failed] == [
        (4, "STT_INCOMPLETE", None),
        (6, "RUBRIC_MISSING", None),
        (7, "RUBRIC_MISSING", None),
    ]
    assert response.take_scores.scored_ratio == 0
    assert [(x.slide_number, x.reason_code) for x in response.detail.scope.excluded] == [
        (4, "STT_INCOMPLETE"),
        (6, "RUBRIC_MISSING"),
        (7, "RUBRIC_MISSING"),
    ]


def test_empty_rubrics_all_rubric_missing_keeps_the_same_422():
    with pytest.raises(CoverageError) as caught:
        _analyze(_request(rubrics=[]))
    assert (caught.value.status_code, caught.value.code) == (422, "RUBRIC_MISSING")
    assert caught.value.message == "채점할 장의 평가 기준이 없습니다."


def test_empty_rubrics_with_script_on_some_slides_is_200():
    request = _with_timing(_request(rubrics=[], stt={4: ""}), {4: INCOMPLETE})
    request["slides"][1]["script"] = _script(6)
    response = _analyze(request)
    assert {f.slide_number: f.error.code for f in response.failed} == {
        4: "STT_INCOMPLETE",
        6: "RUBRIC_MISSING",
        7: "RUBRIC_MISSING",
    }
    assert [f.slide_number for f in response.failed if f.fallback] == [6]


def test_fallback_counts_skip_sentences_that_are_key_point_evidence():
    rubric4 = RUBRICS[0]
    evidence = sorted({i for kp in rubric4.key_points for i in kp.sentence_indices})
    roles = ["skip"] * len(rubric4.sentence_roles)  # 근거가 아닌 skip 문장도 함께 둔다
    changed = rubric4.model_copy(update={"sentence_roles": roles})
    script_norm = normalize_script(changed.normalized_script)
    stt_norm = normalize_script(_script(4))
    sp, tp = positioned_tokens(script_norm), positioned_tokens(stt_norm)
    extra = evidence_skip_alignments(changed, script_norm, sp, stt_norm, tp)
    assert [a.sentence_index for a in extra] == evidence  # 근거 문장만, 다른 skip 은 빠진다
    assert len(roles) > len(evidence)
    assert evidence_skip_alignments(rubric4, script_norm, sp, stt_norm, tp) == []
    kwargs = dict(stt_integrity="unknown", speech_mix=None, reason_code="LLM_TIMEOUT")
    plain = lexical_fallback(changed, [], [], {}, **kwargs)
    with_extra = lexical_fallback(changed, [], [], {}, evidence_alignments=extra, **kwargs)
    assert plain.lexical_coverage is None
    assert with_extra.lexical_coverage == 1.0 and with_extra.delivery_band == "mostly"
    # 실패 장 간이 값을 만드는 흐름에서도 근거 skip 문장을 넘기는지
    flow = _analyze(_request(slide_numbers=[4], rubrics=[changed]), _timeout_on(4))
    assert flow.failed[0].fallback.delivery_band == "mostly"


# ── 4. 말의 구성 ────────────────────────────────────────────


@pytest.mark.parametrize(
    "text",
    [
        "경청해 주셔서 감사합니다",
        "다음 장으로 넘어가겠습니다",
        "여기까지입니다",
        "이상으로 발표를 마치겠습니다",
        "이상입니다",
    ],
)
def test_procedural_phrases_still_match(text):
    assert _is_procedural(text, 3)


@pytest.mark.parametrize(
    "text",
    [
        "경청 능력이 중요합니다",
        "다음으로 매출이 늘었습니다",
        "30% 이상으로 늘었습니다",
        "다음 장점은 가격입니다",
        "생산을 시작하겠습니다",
        "궁금한 점은 가격입니다",
        "효과는 기대 이상입니다",
        "여기까지 오는데 일 년 걸렸습니다",
    ],
)
def test_content_sentences_are_not_procedural(text):
    assert not _is_procedural(text, 3)


def test_unaligned_sentence_candidate_covers_the_whole_sentence():
    stt = "저희 서비스는 발표 연습을 도와줍니다. 이용자는 매주 연습하고 어제 점심에 비빔밥을 먹었습니다."
    norm, ptokens, off = run(stt, [[0]])
    [span] = off.spans
    sentence = norm.sentences[span.stt_sentence_index]
    assert (span.start, span.end) == (0, len(sentence.text))
    assert span.tokens == sum(1 for t in ptokens if t.sentence == span.stt_sentence_index)


def test_said_in_other_slides_needs_the_number_of_the_evidence_sentence():
    others = rubric(OTHER, kps=[("KP2", [1])])  # "월 구독료는 오천 원입니다."
    texts = {"같은 수치": "월 구독료는 오천 원입니다.", "다른 수치": "월 구독료는 만 원입니다."}
    found = {}
    for name, text in texts.items():
        norm, _, off = run(text, [], {2: others})
        assert [s.category for s in off.spans] == ["other_slide"], name
        found[name] = said_in_other_slides(
            {1: (off, [s.text for s in norm.sentences])}, {1: rubric(OTHER), 2: others}
        )
    assert found["같은 수치"] == {(2, "KP2"): [1]}
    assert found["다른 수치"] == {}


# ── 5. 빈틈 ─────────────────────────────────────────────────


def test_said_in_slides_comes_to_slide_and_take_gaps_without_changing_scores():
    plain = _analyze(_request(stt={4: ""}))
    response = _analyze(_request(stt={4: "", 7: _script(7) + " " + _script(4)}))
    four = next(e for e in response.evaluations if e.slide_number == 4)
    assert [(g.key_point_id, g.said_in_slides) for g in four.detail.critical_gaps] == [("KP1", [7])]
    assert [(g.slide_number, g.said_in_slides) for g in response.detail.critical_gaps] == [(4, [7])]
    assert four.scores == next(e for e in plain.evaluations if e.slide_number == 4).scores
    assert four.key_points == next(e for e in plain.evaluations if e.slide_number == 4).key_points


def test_rescore_with_as_stt_answer_keeps_speech_mix_with_offscript_classification(monkeypatch):
    monkeypatch.setattr(stt_core, "OFFSCRIPT_LLM_ENABLED", True)
    stt = {4: "자석" + _script(4)[2:] + " " + ASIDE}
    analyzed = _analyze(_request(stt=stt))
    stored = [e.model_copy(deep=True) for e in analyzed.evaluations]
    stored[0].similar_items = [_item("R1", stored[0].sentences[0].sentence_index)]
    answer = {"slide_number": 4, "item_id": "R1", "answer": "as_stt"}
    again = rescore_evaluation(_rescore_request(stored, [answer]))
    for before, after in zip(analyzed.evaluations, again.evaluations, strict=True):
        assert after.offscript == before.offscript
        assert after.detail.speech_mix == before.detail.speech_mix
    assert again.detail.speech_mix == analyzed.detail.speech_mix
    assert analyzed.detail.speech_mix.categories["off_script"] > 0


def test_old_evaluation_with_unaligned_offscript_sentence_is_rebuilt_by_rescore():
    stt = {4: _script(4) + " " + ASIDE}  # 곁말 두 문장이 짝 없이 붙는다
    analyzed = _analyze(_request(stt=stt))
    fresh = analyzed.evaluations[0].offscript.spans
    old = [
        e.model_copy(update={"detail": None, "offscript": None, "stt_integrity": None})
        for e in analyzed.evaluations
    ]
    again = rescore_evaluation(_rescore_request(old))
    four = again.evaluations[0]
    spans = four.offscript.spans
    assert [(s.id, s.stt_sentence_index, s.category, s.tokens) for s in spans] == [
        ("O1", 5, "off_script", 5),
        ("O2", 6, "off_script", 3),
    ]
    assert [(s.start, s.end) for s in spans] == [(0, 19), (0, 15)]
    assert all(s.end == len(four.stt_sentences[s.stt_sentence_index]) for s in spans)
    assert spans == fresh
    mix = four.detail.speech_mix
    assert mix.categories["off_script"] == 8 and mix.tokens == sum(mix.categories.values())
    assert again.detail.speech_mix.categories == {
        c: sum(e.detail.speech_mix.categories[c] for e in again.evaluations) for c in mix.categories
    }
    assert again.detail.speech_mix.categories["off_script"] == 8


def test_all_failed_200_gives_estimated_slides_with_fallback_band():
    response = _analyze(llms=_timeout_on(*NUMBERS))
    assert response.evaluations == []
    assert [s.slide_number for s in response.detail.slides] == list(NUMBERS)
    for s, f in zip(response.detail.slides, response.failed, strict=True):
        assert s.state == "estimated"
        assert s.reason_codes == ["LLM_TIMEOUT"]
        assert s.delivery is None
        assert s.delivery_band == f.fallback.delivery_band is not None


def test_partial_targets_count_only_target_slides():
    request = _with_timing(
        _request(slide_numbers=[4, 6], stt={6: "", 7: ""}), {6: INCOMPLETE, 7: INCOMPLETE}
    )
    response = _analyze(request)
    assert [e.slide_number for e in response.evaluations] == [4]
    assert [(f.slide_number, f.error.code) for f in response.failed] == [(6, "STT_INCOMPLETE")]
    assert response.detail.scope.target_slides == 2
    assert response.take_scores.scored_ratio == 0.5


def test_rescore_as_stt_answer_gives_exact_delivery_and_contradiction():
    # 4번 장: 중요(가중치 3) contradicted + 보통(가중치 1) covered, 6번 장: 중요(가중치 3) covered
    # 가중치는 최상위 config.SCORE_WEIGHT (critical 3 / normal 1)
    assert (SCORE_WEIGHT["critical"], SCORE_WEIGHT["normal"]) == (3, 1)
    kp2 = (
        BY_SLIDE[4]
        .key_points[0]
        .model_copy(
            update={"id": "KP2", "importance": "normal", "sentence_indices": [1], "fact_ids": []}
        )
    )
    four_rubric = BY_SLIDE[4].model_copy(update={"key_points": [*BY_SLIDE[4].key_points, kp2]})
    rubrics = [four_rubric, BY_SLIDE[6]]
    response = _analyze(
        _request(slide_numbers=[4, 6], rubrics=rubrics, stt={4: "자석" + _script(4)[2:]})
    )
    stored = [e.model_copy(deep=True) for e in response.evaluations]
    stored[0].similar_items = [_item("R1", stored[0].sentences[0].sentence_index)]
    answer = {"slide_number": 4, "item_id": "R1", "answer": "as_stt"}
    after = rescore_evaluation(_rescore_request(stored, [answer], rubrics=rubrics))
    four, six = (e.detail for e in after.evaluations)
    # 4번 장: delivery = (0×3 + 1×1) / 4 = 0.25, 다르게 말함 = 3 / 4 = 0.75
    assert (four.delivery.value, four.contradiction.value) == (0.25, 0.75)
    assert (six.delivery.value, six.contradiction.value) == (1.0, 0.0)
    # Take (장 가중치 합 4 + 3 = 7): delivery = (0×3 + 1×1 + 1×3) / 7 = 0.571, 다르게 말함 = 3 / 7 = 0.429
    assert (after.detail.delivery.value, after.detail.contradiction.value) == (0.571, 0.429)
    assert after.detail.status_shares["contradicted"] == 0.429
