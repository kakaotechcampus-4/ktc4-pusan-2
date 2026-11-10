"""장 상세: 전달률 · 0 의 종류 · null 규칙 · 상태 전파 · STT 완전성 · 표현 방식 (손으로 만든 평가)."""

import pytest

from pitch_coach_ai.features.script_coverage.stt_evaluation import detail as detail_module
from pitch_coach_ai.features.script_coverage.stt_evaluation.detail import (
    annotate_expression,
    expression_mode,
    slide_detail,
    status_shares,
    stt_integrity,
)
from pitch_coach_ai.features.script_coverage.stt_evaluation.schemas import (
    FactCheck,
    KeyPointResult,
    SentenceResult,
    SlideEvaluation,
    SpeechMix,
)

from ..fakes import deck_rubrics

RUBRIC = deck_rubrics((4,))[0]
MIX = SpeechMix.model_validate(
    {
        "state": "ok",
        "tokens": 0,
        "categories": {},
        "display": [],
        "repetitions": {"count": 0},
    }
)


def kp(kid: str, importance: str, status: str) -> KeyPointResult:
    return KeyPointResult(
        key_point_id=kid,
        content=f"내용 {kid}",
        importance=importance,
        sentence_indices=[0],
        status=status,
        first_status=status,
        evidence=[],
        evidence_text="",
        reason="",
        lexical_coverage=0.0,
        fact_statuses={},
    )


def sentence(status: str, coverage: float) -> SentenceResult:
    return SentenceResult(
        sentence_index=0,
        text="문장",
        role="claim",
        status=status,
        first_status=status,
        confidence="high",
        evidence=[],
        reason="",
        lexical_coverage=coverage,
        evidence_coverage=0.0,
        fact_statuses={},
    )


def fact(fid: str, status: str, importance: str = "normal") -> FactCheck:
    return FactCheck(
        fact_id=fid,
        type="number",
        value="3",
        normalized="3",
        importance=importance,
        key_point_ids=[],
        status=status,
    )


def evaluation(kps, *, stt_text="말함", facts=(), sentences=(), fidelity=None, accuracy=None):
    return SlideEvaluation(
        take_id="t",
        script_name="s",
        slide_number=1,
        rubric_id=RUBRIC.meta.rubric_id,
        scores={"critical_fact_accuracy": accuracy},
        sentences=list(sentences),
        key_points=list(kps),
        critical_facts=list(facts),
        similar_items=[],
        fidelity=fidelity or {"recall": 0.62, "precision": 0.81, "script_tokens": 24},
        alignments=[],
        verification={},
        stt_sentences=[stt_text] if stt_text else [],
        stt_text=stt_text,
    )


def detail(e, rubric=RUBRIC, integrity="complete", said=None):
    return slide_detail(e, rubric, stt_integrity=integrity, speech_mix=MIX, said=said)


EXAMPLE = [
    kp("KP1", "critical", "covered"),
    kp("KP2", "high", "contradicted"),
    kp("KP3", "normal", "missing"),
]


def test_contract_example_numbers():
    d = detail(evaluation(EXAMPLE), said={"KP2": [5, 3]})
    assert d.delivery.model_dump() == {
        "value": 0.5,
        "state": "ok",
        "reason_codes": [],
        "evaluated": 3,
        "excluded": 0,
    }
    assert d.contradiction.value == 0.333
    assert d.status_shares == {
        "covered": 0.5,
        "partial": 0.0,
        "missing": 0.167,
        "contradicted": 0.333,
    }
    assert d.legacy.content_coverage_raw == 0.333 and d.legacy.zero_kind is None
    assert d.zero_kind is None
    assert d.key_points["critical"] == {"covered": 1, "partial": 0, "missing": 0, "contradicted": 0}
    assert d.key_points["normal"]["missing"] == 1
    [gap] = d.critical_gaps  # normal 인 KP3 은 목록에 없다
    assert (gap.key_point_id, gap.status, gap.said_in_slides) == ("KP2", "contradicted", [3, 5])


def test_status_shares_sum_exactly_one():
    shares = status_shares({"covered": 1, "partial": 1, "missing": 1, "contradicted": 0})
    assert round(sum(shares.values()), 3) == 1.0
    assert (
        status_shares(dict.fromkeys(("covered", "partial", "missing", "contradicted"), 0)) is None
    )


def test_no_key_points_is_null_not_zero():
    d = detail(evaluation([]))
    for m in (d.delivery, d.contradiction):
        assert (m.value, m.state, m.reason_codes) == (None, "not_applicable", ["NO_KEY_POINTS"])
    assert d.status_shares is None and d.zero_kind is None and d.legacy.content_coverage_raw is None


def test_zero_kinds():
    missing = [kp("KP1", "critical", "missing"), kp("KP2", "normal", "missing")]
    assert detail(evaluation(missing)).zero_kind == "all_missing"
    assert detail(evaluation(missing, stt_text="")).zero_kind == "slide_skipped"
    mixed = [kp("KP1", "critical", "missing"), kp("KP2", "normal", "contradicted")]
    assert detail(evaluation(mixed)).zero_kind == "missing_or_contradicted"
    assert (
        detail(evaluation([kp("KP1", "high", "contradicted")])).zero_kind
        == "missing_or_contradicted"
    )


def test_legacy_cancelled_and_clamped():
    # critical covered(3) + critical contradicted(-1.5): 기존 0.5 → 새 전달률 0.5, 상쇄 아님
    d = detail(evaluation([kp("A", "critical", "covered"), kp("B", "critical", "contradicted")]))
    assert d.legacy.content_coverage_raw == 0.25 and d.legacy.zero_kind is None
    # covered 1 + contradicted 2배 가중 → 기존 0 (상쇄), 새 전달률은 양수
    cancelled = detail(evaluation([kp("A", "normal", "covered"), kp("B", "high", "contradicted")]))
    assert cancelled.legacy.content_coverage_raw == 0.0
    assert cancelled.legacy.zero_kind == "cancelled_to_zero" and cancelled.delivery.value == 0.333
    clamped = detail(
        evaluation([kp("A", "normal", "covered"), kp("B", "critical", "contradicted")])
    )
    assert clamped.legacy.zero_kind == "clamped_negative"
    assert clamped.legacy.content_coverage_raw < 0 and clamped.delivery.value == 0.25


def test_state_propagation(monkeypatch):
    fallback_rubric = RUBRIC.model_copy(
        update={"meta": RUBRIC.meta.model_copy(update={"rubric_quality": "sentence_fallback"})}
    )
    # 기본값: 복구 실험(11)이 기준을 통과해 문장 단위 복구 기준도 정상(ok)이다
    d = detail(evaluation(EXAMPLE), fallback_rubric)
    assert (d.state, d.reason_codes, d.rubric_quality) == ("ok", [], "sentence_fallback")
    # 설정으로 켜면 잠정(provisional)으로 표시한다
    monkeypatch.setattr(detail_module, "SENTENCE_FALLBACK_PROVISIONAL", True)
    d = detail(evaluation(EXAMPLE), fallback_rubric)
    assert (d.state, d.reason_codes, d.rubric_quality) == (
        "provisional",
        ["SENTENCE_FALLBACK"],
        "sentence_fallback",
    )
    assert d.delivery.state == "provisional" and d.delivery.reason_codes == ["SENTENCE_FALLBACK"]
    monkeypatch.undo()
    d = detail(evaluation(EXAMPLE), integrity="incomplete")
    assert d.reason_codes == ["STT_INCOMPLETE"] and d.contradiction.state == "provisional"
    # STT 가 비고 확인 못 함
    d = detail(evaluation(EXAMPLE, stt_text=""), integrity="unknown")
    assert d.reason_codes == ["STT_UNVERIFIED"] and d.zero_kind is None
    d = detail(evaluation([kp("A", "normal", "missing")], stt_text=""), integrity="unknown")
    assert d.zero_kind == "slide_skipped" and d.delivery.reason_codes == ["STT_UNVERIFIED"]
    # 핵심 내용이 없는 null 은 상태를 따르지 않는다
    d = detail(evaluation([], stt_text=""), integrity="unknown")
    assert d.state == "provisional" and d.delivery.state == "not_applicable"


def test_facts_states_and_range():
    checks = [fact("1", "matched"), fact("2", "matched"), fact("3", "sound_alike")]
    d = detail(evaluation(EXAMPLE, facts=checks, accuracy=1.0))
    a = d.facts.accuracy
    assert (a.value, a.state, a.reason_codes, a.evaluated, a.excluded) == (
        1.0,
        "provisional",
        ["FACTS_PENDING"],
        2,
        1,
    )
    assert d.facts.accuracy_range == {"best": 1.0, "worst": 0.667}
    assert d.facts.pending == 1
    only_pending = detail(evaluation(EXAMPLE, facts=[fact("1", "sound_alike")], accuracy=None))
    assert only_pending.facts.accuracy.state == "pending"
    assert only_pending.facts.accuracy.reason_codes == ["ALL_FACTS_PENDING"]
    assert only_pending.facts.accuracy_range == {"best": 1.0, "worst": 0.0}
    none = detail(evaluation(EXAMPLE))
    assert (none.facts.accuracy.state, none.facts.accuracy.reason_codes) == (
        "not_applicable",
        ["NO_FACTS"],
    )
    assert none.facts.accuracy_range is None
    full = detail(
        evaluation(EXAMPLE, facts=[fact("1", "mismatched"), fact("2", "unverified")], accuracy=0.0)
    )
    assert (
        full.facts.accuracy.state == "ok" and full.facts.missing == 1
    )  # unverified 는 점수대로 0점


def test_expression_modes():
    s = [
        sentence("said", 0.95),
        sentence("said", 0.5),
        sentence("said", 0.85),
        sentence("missing", 1.0),
    ]
    annotate_expression(s)
    assert [x.expression_mode for x in s] == ["verbatim_like", "paraphrased", "undetermined", None]
    d = detail(evaluation(EXAMPLE, sentences=s))
    assert d.expression.model_dump() == {"verbatim_like": 1, "paraphrased": 1, "undetermined": 1}
    assert expression_mode(sentence("said", 0.9)) == "verbatim_like"
    assert expression_mode(sentence("said", 0.8)) == "undetermined"


def test_fidelity_null_when_script_has_no_tokens():
    d = detail(evaluation(EXAMPLE, fidelity={"recall": 0.0, "precision": 0.0, "script_tokens": 0}))
    assert d.fidelity.recall.value is None and d.fidelity.precision.state == "not_applicable"
    d = detail(evaluation(EXAMPLE))
    assert (d.fidelity.recall.value, d.fidelity.recall.evaluated) == (0.62, 24)
    assert d.fidelity.precision.value == 0.81 and d.fidelity.precision.evaluated > 0


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        ((None, 0, 0), "unknown"),
        ((1000, None, 0), "unknown"),
        ((1000, 0, None), "unknown"),
        ((0, 5000, 5000), "complete"),
        ((10000, 0, 5001), "unknown"),  # 기록 없는 시간 비율 > 0.5
        ((10000, 0, 5000), "complete"),
        ((60000, 5999, 0), "complete"),  # 6000 = max(2000, 0.1 × 60000) 미만
        ((60000, 6000, 0), "incomplete"),
        ((5000, 1999, 0), "complete"),  # 2000 = max(2000, 500) 미만
        ((5000, 2000, 0), "incomplete"),
    ],
)
def test_stt_integrity_boundaries(args, expected):
    assert stt_integrity(*args) == expected
