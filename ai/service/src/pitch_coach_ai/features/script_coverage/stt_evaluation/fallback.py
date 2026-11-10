"""실패 장 간이 값: 채점이 실패한 장에 규칙만으로 내는 대략의 전달 정도 (확정 값과 합치지 않는다).

- `lexical_fallback`: 평가 기준이 있는 장 (`method: lexical`). 문장 정렬의 `coverage` 를 1 · 0.5 · 0 으로 바꿔 평균한다
- `no_rubric_fallback`: 평가 기준이 없고 대본 글(`script`)만 있는 장 (`method: lexical_no_rubric`). 중요도 없이 계산한다

둘 다 LLM 을 부르지 않는다. 숫자 대신 3단계(`delivery_band`)를 화면에 보인다.
"""

from collections.abc import Sequence

from ..shared.facts import extract_critical_facts
from ..shared.rubric import EvaluationRubric, RubricMeta
from ..shared.text import NormalizedScript, normalize_script
from .align import NUMERIC_TYPES, Token, align_sentences, fidelity_excluding, positioned_tokens
from .config import (
    FALLBACK_BAND_LITTLE,
    FALLBACK_BAND_MOSTLY,
    FALLBACK_SENTENCE_HI,
    FALLBACK_SENTENCE_LO,
)
from .detail import fact_counts, fact_parts
from .fact_check import check_critical_facts
from .normalize import normalize_stt
from .schemas import Alignment, FactCheck, Fallback, Metric, SpeechMix


def _lexical_coverage(alignments: list[Alignment]) -> float | None:
    """문장마다 정렬 `coverage` 를 HI 이상 1 · LO 이상 0.5 · 그 밖 0 으로 바꿔 낸 평균. 문장이 없으면 None."""
    if not alignments:
        return None
    points = [
        1.0
        if a.coverage >= FALLBACK_SENTENCE_HI
        else 0.5
        if a.coverage >= FALLBACK_SENTENCE_LO
        else 0.0
        for a in alignments
    ]
    return round(sum(points) / len(points), 3)


def _band(value: float | None) -> str | None:
    """간이 전달률 → `mostly` (MOSTLY 이상) · `little` (LITTLE 미만) · `about_half`. None 이면 None."""
    if value is None:
        return None
    if value >= FALLBACK_BAND_MOSTLY:
        return "mostly"
    if value < FALLBACK_BAND_LITTLE:
        return "little"
    return "about_half"


def evidence_skip_alignments(
    rubric: EvaluationRubric,
    script_norm: NormalizedScript,
    script_ptokens: list[Token],
    stt_norm: NormalizedScript,
    stt_ptokens: list[Token],
) -> list[Alignment]:
    """핵심 내용의 근거 문장(`key_points[].sentence_indices`)인데 역할이 skip 인 문장의 정렬 (그런 문장이 없으면 빈 목록).

    기본 정렬은 skip 문장을 건너뛰므로, 그 문장만 `detail` 로 보고 따로 맞춘다. 판정(`judge`)이 이 문장을 평가하는 것과 같은 규칙이다.
    """
    evidence = {i for kp in rubric.key_points for i in kp.sentence_indices}
    roles = [
        "detail" if role == "skip" and i in evidence else "skip"
        for i, role in enumerate(rubric.sentence_roles)
    ]
    if "detail" not in roles:
        return []
    return align_sentences(
        script_norm,
        [(t.key, t.sentence) for t in script_ptokens],
        stt_norm,
        [(t.key, t.sentence) for t in stt_ptokens],
        roles,
    )


def lexical_fallback(
    rubric: EvaluationRubric,
    alignments: list[Alignment],
    fact_checks: list[FactCheck],
    fidelity: dict,
    *,
    stt_integrity: str,
    speech_mix: SpeechMix | None,
    reason_code: str,
    evidence_alignments: Sequence[Alignment] = (),
) -> Fallback:
    """평가 기준이 있는 실패 장의 간이 값.

    skip 이 아닌 문장과 핵심 내용의 근거인 skip 문장(`evidence_alignments`, `evidence_skip_alignments` 로 만든다)의 정렬 `coverage` 로 `lexical_coverage` · `delivery_band` 를 내고, 수치 개수 · 수치 정확도
    (점수와 같은 계산, `estimated`) · 대본 재현(`fidelity` 의 recall, 대본 형태소가 0 이면 None)을 더한다.
    """
    roles = rubric.sentence_roles
    coverage = _lexical_coverage(
        [a for a in alignments if roles[a.sentence_index] != "skip"] + list(evidence_alignments)
    )
    return _build(
        "lexical",
        coverage,
        fact_checks,
        fidelity,
        stt_integrity=stt_integrity,
        speech_mix=speech_mix,
        reason_codes=[reason_code],
    )


def _build(
    method: str,
    coverage: float | None,
    fact_checks: list[FactCheck],
    fidelity: dict,
    *,
    stt_integrity: str,
    speech_mix: SpeechMix | None,
    reason_codes: list[str],
) -> Fallback:
    parts = fact_parts(fact_checks)
    accuracy = (
        Metric(
            value=round(parts["hit"] / parts["weight"], 3),
            state="estimated",
            evaluated=parts["counted"],
            excluded=parts["pending"],
        )
        if parts["weight"]
        else None
    )
    script_tokens = fidelity.get("script_tokens", 0)
    recall = (
        Metric(
            value=fidelity["recall"],
            state="estimated",
            evaluated=script_tokens,
        )
        if script_tokens
        else None
    )
    return Fallback(
        method=method,
        delivery_band=_band(coverage),
        lexical_coverage=coverage,
        facts=fact_counts(fact_checks) if fact_checks else None,
        fact_accuracy=accuracy,
        script_recall=recall,
        speech_mix=speech_mix,
        stt_integrity=stt_integrity,
        reason_codes=reason_codes,
    )


def no_rubric_fallback(script: str, stt: str, *, stt_integrity: str) -> Fallback:
    """평가 기준이 없는 장의 간이 값 (`lexical_no_rubric`, 이유 `RUBRIC_MISSING`).

    대본 글을 정규화해 모든 문장을 `detail` 역할, 핵심 내용 없음, 수치 중요도 `normal` 인 임시 평가 기준으로 보고
    `lexical_fallback` 과 같이 계산한다. 이름·용어는 LLM 판정 없이는 믿을 수 없어 수치(숫자 종류)만 센다.
    `speech_mix` 는 None. 대본이 비면 `lexical_coverage` · `delivery_band` 가 None 이다.
    """
    script_norm = normalize_script(script)
    stt_norm = normalize_stt(stt)
    roles = ["detail"] * len(script_norm.sentences)
    facts = [f for f in extract_critical_facts(script_norm) if f.type in NUMERIC_TYPES]
    rubric = EvaluationRubric(
        meta=RubricMeta(
            rubric_id="",
            script_name="",
            slide_number=0,
            content_hash="",
            rubric_schema_version="",
            llm_model="",
            llm_config_hash="",
        ),
        normalized_script=script_norm.text,
        sentences=script_norm.sentences,
        sentence_roles=roles,
        core_claim="",
        key_points=[],
        critical_facts=facts,
        keywords=[],
        warnings=[],
    )
    script_ptokens, stt_ptokens = positioned_tokens(script_norm), positioned_tokens(stt_norm)
    alignments = align_sentences(
        script_norm,
        [(t.key, t.sentence) for t in script_ptokens],
        stt_norm,
        [(t.key, t.sentence) for t in stt_ptokens],
        roles,
    )
    fact_checks = check_critical_facts(rubric, stt_norm, alignments)
    fidelity = fidelity_excluding(rubric, script_ptokens, stt_ptokens, [])
    return _build(
        "lexical_no_rubric",
        _lexical_coverage(alignments),
        fact_checks,
        fidelity,
        stt_integrity=stt_integrity,
        speech_mix=None,
        reason_codes=["RUBRIC_MISSING"],
    )
