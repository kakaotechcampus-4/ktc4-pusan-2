"""결과 병합: LLM 판정과 규칙 결과를 합치고 충돌을 찾는다. 전달 단위 → 문장 → Key Point 판정은 코드가 모은다."""

from collections import Counter

from ..shared.facts import CriticalFact
from ..shared.rubric import NAME_TYPES, EvaluationRubric
from ..shared.text import NormalizedScript, compact
from .align import NUMERIC_TYPES, Token
from .config import LEXICAL_ABSENT, LEXICAL_COMPLETE, LEXICAL_PRESENT
from .judge import judged_sentences, sentence_units
from .schemas import (
    Alignment,
    FactCheck,
    KeyPointResult,
    SemanticEvaluation,
    SentenceJudgment,
    SentenceResult,
    SimilarItem,
    UnitResult,
)

CONFLICT_TEXT = {
    "contradicted": "모순 판정은 항상 다시 확인",
    "low_confidence": "LLM 확신도가 high 가 아님",
    "judgment_incomplete": "LLM 이 이 문장의 전달 단위 일부를 판정하지 않음",
    "evidence_invalid": "전달됐다고 했는데 근거 STT 문장이 없음",
    "evidence_unrelated": "근거로 든 STT 문장에 이 문장의 단어·핵심 수치·이름이 거의 없음",
    "fact_missing": "said 인데 이 문장의 핵심 수치·이름이 STT 에 없음",
    "sound_alike": "발음이 비슷한 다른 말이 있는데 said 가 아님 — 그 차이는 빼고(대본대로 말한 것으로 보고) 다시 판정",
    "speaker_changed": "발음이 전혀 다른 수치를 말했는데 contradicted 가 아님",
    "approximation": "수치를 어림해 말했는데 said",
    "partial_complete": "partial 인데 빠지거나 어림한 핵심 수치·이름이 없고 이 문장의 단어가 STT 에 대부분 나옴",
    "fact_present": "missing 인데 이 문장에만 있는 핵심 수치를 STT 에서 찾음",
    "lexical_present": "missing 인데 이 문장의 단어가 STT 에 많이 나옴",
    "lexical_absent": "said 인데 이 문장의 단어가 STT 에 거의 없음",
}


def status_from_units(statuses: list[str]) -> str:
    """전달 단위 판정 → 문장 판정: 다르게 말한 단위가 있으면 contradicted, 모두 said 면 said, 모두 missing 이면 missing,
    나머지(흐리게 말한 단위 포함)는 partial. '일부 전달'의 경계를 LLM 이 아니라 코드가 정한다."""
    if not statuses:
        return "missing"
    if "contradicted" in statuses:
        return "contradicted"
    if all(s == "said" for s in statuses):
        return "said"
    if all(s == "missing" for s in statuses):
        return "missing"
    return "partial"


FOUND_STATUSES = {
    "matched",
    "approximate",
    "sound_alike",
}  # 규칙이 STT 에서 그 수치·이름(또는 발음이 비슷한 말)을 찾은 결과


def facts_said_here(
    checks: dict[str, FactCheck], facts: dict[str, CriticalFact], i: int
) -> set[str]:
    """이 문장에만 있는 수치·이름 중 규칙이 STT 에서 찾은 것. 여러 문장에 나오는 사실은 다른 문장을 말하면서 나왔을 수 있어 뺀다."""
    return {
        fid
        for fid, c in checks.items()
        if c.status in FOUND_STATUSES and set(facts[fid].sentence_indices) == {i}
    }


def raise_units_with_facts(units: list[UnitResult], found: set[str]) -> None:
    """단위 판정 보정 (규칙): 단위의 수치·이름을 규칙이 STT 에서 찾았는데 missing 이면 vague 로 올린다.
    이름·수치를 말했다면 그 단위가 '전혀 없음'일 수는 없다 (규칙의 사실 검증은 결정적이고 정확하다)."""
    for u in units:
        if u.status == "missing" and found & set(u.fact_ids):
            u.status, u.raised_by_rule = "vague", True


def aggregate_status(statuses: list[str]) -> str:
    """문장 판정 → Key Point 판정: 모순이 하나라도 있으면 contradicted, 모두 said 면 covered, 모두 missing 이면 missing, 나머지 partial."""
    if not statuses:
        return "missing"
    if "contradicted" in statuses:
        return "contradicted"
    if all(s == "said" for s in statuses):
        return "covered"
    if all(s == "missing" for s in statuses):
        return "missing"
    return "partial"


def resolve_name_checks(
    fact_checks: list[FactCheck], unverified: list[FactCheck], semantic: SemanticEvaluation
) -> None:
    """규칙으로 못 찾은 영문 이름(unverified)을 LLM 이름 확인 결과로 확정한다."""
    said = {c.name_id: c for c in semantic.name_checks}
    for i, check in enumerate(unverified, 1):
        result = said.get(f"N{i}")
        if result is not None and result.said:
            check.status, check.stt_ids, check.note = (
                "matched",
                result.evidence,
                "LLM 확인: 한글 표기 등으로 말함",
            )
        else:
            check.status, check.note = "missing", "LLM 확인: 말하지 않음"


def evidence_coverage(
    script_ptokens: list[Token], stt_ptokens: list[Token], sentence_index: int, evidence: list[int]
) -> float:
    """대본 문장의 내용 형태소 중 LLM 이 근거로 든 STT 문장들에 나온 비율. `align_sentences` 의 coverage 와 같은 계산을 정렬 구간 대신 근거 문장에 한다."""
    target = Counter(t.key for t in script_ptokens if t.sentence == sentence_index)
    found = Counter(t.key for t in stt_ptokens if t.sentence in evidence)
    return sum((target & found).values()) / sum(target.values()) if target else 0.0


def facts_in_evidence(
    checks: list[FactCheck], evidence: list[int], stt_norm: NormalizedScript
) -> bool:
    """이 문장의 수치·이름이 근거 문장에 나왔는가. 수치는 `check_critical_facts` 가 찾은 STT 문장 번호로, 이름은 근거 문장 텍스트에서 찾는다."""
    text = compact(" ".join(stt_norm.sentences[t].text for t in evidence))[0]
    return any(
        set(c.stt_ids) & set(evidence) or (c.type in NAME_TYPES and compact(c.value)[0] in text)
        for c in checks
    )


def merge_sentences(
    rubric: EvaluationRubric,
    semantic: SemanticEvaluation,
    fact_checks: list[FactCheck],
    alignments: list[Alignment],
    stt_norm: NormalizedScript,
    items: list[SimilarItem],
    script_ptokens: list[Token],
    stt_ptokens: list[Token],
) -> list[SentenceResult]:
    """Result Merger + Conflict Check (코드): 대본 문장마다 LLM 판정과 규칙 결과를 합치고, 어긋나면 이유를 남긴다."""
    judgments = {j.sentence_id: j for j in semantic.sentences}
    coverage = {a.sentence_index: a.coverage for a in alignments}
    facts = {f.id: f for f in rubric.critical_facts}
    n_stt = len(stt_norm.sentences)
    results = []
    for i in judged_sentences(rubric):
        j = judgments.get(i) or SentenceJudgment(
            sentence_id=i, evidence=[], reason="LLM 판정 누락", units=[], confidence="low"
        )
        # 문장 판정 = 전달 단위 판정을 모은 것. LLM 이 판정하지 않은 단위는 missing 으로 두고 충돌로 표시한다
        judged = {u.unit_id.strip().upper(): u.status for u in j.units}
        checks = {c.fact_id: c for c in fact_checks if i in facts[c.fact_id].sentence_indices}
        units = [
            UnitResult(
                unit_id=u.id,
                text=u.text,
                fact_ids=u.fact_ids,
                status=judged.get(u.id.upper(), "missing"),
                first_status="missing",
            )
            for u in sentence_units(rubric, i)
        ]
        raise_units_with_facts(units, facts_said_here(checks, facts, i))
        for u in units:
            u.first_status = u.status
        status = status_from_units([u.status for u in units])
        evidence = sorted({t for t in j.evidence if 0 <= t < n_stt})
        lexical = coverage.get(i, 0.0)
        evidence_cov = evidence_coverage(script_ptokens, stt_ptokens, i, evidence)
        statuses = {fid: c.status for fid, c in checks.items()}
        similar = [it for it in items if it.script_sentence_index == i]

        conflicts = []
        if status == "contradicted":
            conflicts.append("contradicted")
        # LLM 이 스스로 매긴 확신도. low 는 거의 나오지 않고 medium 의 오답 비율이 high 보다 훨씬 높아서 high 가 아니면 다시 본다
        if j.confidence != "high":
            conflicts.append("low_confidence")
        if any(u.unit_id.upper() not in judged for u in units):
            conflicts.append("judgment_incomplete")
        if status != "missing" and not evidence:
            conflicts.append("evidence_invalid")
        elif (
            status != "missing"
            and evidence_cov < LEXICAL_ABSENT
            and not facts_in_evidence(list(checks.values()), evidence, stt_norm)
        ):
            # 근거 번호는 맞지만 그 문장에 이 문장의 내용이 없다: 다른 내용을 근거로 들었거나, partial 인데 실제로 말한 부분이 없다
            conflicts.append("evidence_unrelated")
        if status == "said" and "missing" in statuses.values():
            conflicts.append("fact_missing")
        # 비슷한 말(`similar.py`)은 판단 보류: 그 차이 때문에 낮게 판정했을 수 있으면 빼고 다시 판정한다
        if similar and status != "said":
            conflicts.append("sound_alike")
        if "mismatched" in statuses.values() and status != "contradicted":
            conflicts.append("speaker_changed")
        if "approximate" in statuses.values() and status == "said":
            conflicts.append("approximation")
        # partial 은 빠진 부분이 있어야 한다: 빠지거나 어림한 수치·이름도 없고 단어도 거의 다 나왔으면 said 일 수 있다
        if (
            status == "partial"
            and not set(statuses.values()) & {"missing", "approximate", "mismatched"}
            and lexical >= LEXICAL_COMPLETE
        ):
            conflicts.append("partial_complete")
        # 여러 문장에 나오는 수치는 다른 문장을 말하면서 나왔을 수 있어서, 이 문장에만 있는 수치만 본다
        if status == "missing" and any(
            c.status == "matched"
            and c.type in NUMERIC_TYPES
            and facts[c.fact_id].sentence_indices == [i]
            for c in checks.values()
        ):
            conflicts.append("fact_present")
        if status == "missing" and lexical >= LEXICAL_PRESENT:
            conflicts.append("lexical_present")
        if status == "said" and lexical < LEXICAL_ABSENT:
            conflicts.append("lexical_absent")

        results.append(
            SentenceResult(
                sentence_index=i,
                text=rubric.sentences[i].text,
                role=rubric.sentence_roles[i],
                status=status,
                first_status=status,
                units=units,
                confidence=j.confidence,
                evidence=evidence,
                reason=j.reason,
                lexical_coverage=round(lexical, 3),
                evidence_coverage=round(evidence_cov, 3),
                fact_statuses=statuses,
                similar_ids=[it.item_id for it in similar],
                conflicts=conflicts,
            )
        )
    return results


def aggregate_key_points(
    rubric: EvaluationRubric,
    sentences: list[SentenceResult],
    fact_checks: list[FactCheck],
    items: list[SimilarItem],
    stt_norm: NormalizedScript,
) -> list[KeyPointResult]:
    """Key Point 판정 = 근거 문장 판정을 모은 것. 1차(LLM 문장 판정)와 최종(교차 검증 반영)을 각각 모은다."""
    by_index = {s.sentence_index: s for s in sentences}
    checks = {c.fact_id: c for c in fact_checks}
    results = []
    for kp in rubric.key_points:
        own = [by_index[i] for i in kp.sentence_indices if i in by_index]
        evidence = sorted({t for s in own for t in s.evidence})
        not_said = [
            f"S{s.sentence_index} {s.status}: {s.reason}" for s in own if s.status != "said"
        ]
        results.append(
            KeyPointResult(
                key_point_id=kp.id,
                content=kp.content,
                importance=kp.importance,
                sentence_indices=kp.sentence_indices,
                status=aggregate_status([s.status for s in own]),
                first_status=aggregate_status([s.first_status for s in own]),
                evidence=evidence,
                evidence_text=" ".join(stt_norm.sentences[t].text for t in evidence),
                reason=" / ".join(not_said) or "근거 문장을 모두 전달함",
                lexical_coverage=round(sum(s.lexical_coverage for s in own) / max(len(own), 1), 3),
                fact_statuses={fid: checks[fid].status for fid in kp.fact_ids if fid in checks},
                similar_ids=[it.item_id for it in items if kp.id in it.key_point_ids],
                conflicts=sorted({c for s in own for c in s.conflicts}),
                verified=any(s.verified for s in own),
            )
        )
    return results
