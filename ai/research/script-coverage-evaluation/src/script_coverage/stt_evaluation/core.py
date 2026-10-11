"""STT 평가 진입점: 발표 연습 한 번(STT)을 대본의 평가 기준으로 채점한다.

LLM 과 캐시는 인자로 받는다. 평가 기준(rubric)을 읽고 결과를 저장하는 일은 호출하는 쪽이 한다.
"""

from datetime import UTC, datetime

from ..shared.llm_step import LLMCache, llm_step
from ..shared.rubric import EvaluationRubric
from ..shared.text import normalize_script
from .align import align_sentences, fidelity_excluding, positioned_tokens
from .fact_check import annotate_mismatches, check_critical_facts
from .judge import eval_config_hash, evaluate_semantics, semantic_eval_message
from .merge import (
    CONFLICT_TEXT,
    aggregate_key_points,
    merge_sentences,
    resolve_name_checks,
)
from .normalize import normalize_stt
from .schemas import SemanticEvaluation, SlideEvaluation, Take, VerifierResult
from .scoring import slide_scores
from .similar import find_similar_items, settle_facts
from .verify import apply_verification, verifier_config_hash, verifier_message, verify


def evaluate_take(
    take: Take,
    rubrics: dict[int, EvaluationRubric],
    *,
    eval_llm,
    verifier_llm,
    model: str,
    cache: LLMCache | None = None,
    max_workers: int = 4,
) -> tuple[list[SlideEvaluation], dict]:
    """Presentation Evaluation Pipeline. 발표 연습 한 번(STT)을 대본의 평가 기준으로 채점한다.

    ① STT 정규화 → ② 규칙 분석 (문장 정렬 · 핵심 사실 검증 · 비슷한 말 찾기 · 대본 충실도) ∥ LLM 의미 평가 (대본 문장별, 슬라이드당 1회)
    → ③ 문장별 병합·충돌 검사 (코드) → ④ 충돌한 문장이 있는 슬라이드만 LLM 교차 검증 (슬라이드당 1회)
    → ⑤ Key Point 판정 = 문장 판정 모으기 (코드) → 점수 계산 (코드).
    `rubrics` = {슬라이드 번호: 평가 기준}. 대본을 다시 분석하지 않고 주어진 평가 기준을 읽어 쓴다.
    결과(평가 목록)는 저장하지 않고 돌려준다 — 저장은 호출하는 쪽이 한다.
    """
    slides = {}
    for s in take.slides:
        rubric = rubrics.get(s.slide_number)
        if rubric is None:
            raise ValueError(
                f"{take.script_name} 슬라이드 {s.slide_number} 의 평가 기준이 없다 — 대본 분석을 먼저 실행한다"
            )
        script_norm = normalize_script(rubric.normalized_script)
        stt_norm = normalize_stt(s.stt)
        script_ptokens, stt_ptokens = positioned_tokens(script_norm), positioned_tokens(stt_norm)
        alignments = align_sentences(
            script_norm,
            [(t.key, t.sentence) for t in script_ptokens],
            stt_norm,
            [(t.key, t.sentence) for t in stt_ptokens],
            rubric.sentence_roles,
        )
        fact_checks = check_critical_facts(rubric, stt_norm, alignments)
        annotate_mismatches(fact_checks, rubric, stt_norm)
        items = find_similar_items(
            rubric,
            script_norm,
            script_ptokens,
            stt_norm,
            stt_ptokens,
            alignments,
            fact_checks,
            s.stt,
        )
        settle_facts(fact_checks, items)
        # 대본 충실도: 비슷한 말 자리의 형태소는 양쪽에서 뺀다
        fidelity = fidelity_excluding(rubric, script_ptokens, stt_ptokens, items)
        unverified = [c for c in fact_checks if c.status == "unverified"]
        slides[s.slide_number] = dict(
            rubric=rubric,
            script_norm=script_norm,
            stt_norm=stt_norm,
            alignments=alignments,
            fidelity=fidelity,
            fact_checks=fact_checks,
            unverified=unverified,
            items=items,
            script_ptokens=script_ptokens,
            stt_ptokens=stt_ptokens,
        )

    # ② LLM 의미 평가 (규칙 분석과 독립: 규칙 결과를 입력에 넣지 않는다)
    messages = {
        n: semantic_eval_message(n, d["rubric"], d["stt_norm"], d["unverified"])
        for n, d in slides.items()
    }
    semantics, sem_errors, sem_calls = llm_step(
        "stt.semantic",
        messages,
        evaluate_semantics,
        eval_llm,
        eval_config_hash(model),
        SemanticEvaluation,
        cache=cache,
        max_workers=max_workers,
    )

    # ③ 문장별 병합·충돌 검사
    for n, d in slides.items():
        if n in sem_errors:
            continue
        resolve_name_checks(d["fact_checks"], d["unverified"], semantics[n])
        d["sentences"] = merge_sentences(
            d["rubric"],
            semantics[n],
            d["fact_checks"],
            d["alignments"],
            d["stt_norm"],
            d["items"],
            d["script_ptokens"],
            d["stt_ptokens"],
        )

    # ④ 교차 검증: 충돌한 문장이 있는 슬라이드만, 슬라이드마다 한 번
    ver_messages = {
        n: verifier_message(
            d["rubric"],
            d["sentences"],
            d["fact_checks"],
            d["alignments"],
            d["stt_norm"],
            d["items"],
        )
        for n, d in slides.items()
        if "sentences" in d and any(s.conflicts for s in d["sentences"])
    }
    verified, ver_errors, ver_calls = llm_step(
        "stt.verifier",
        ver_messages,
        verify,
        verifier_llm,
        verifier_config_hash(model),
        VerifierResult,
        cache=cache,
        max_workers=max_workers,
    )

    # ⑤ Key Point 판정(문장 판정 모으기) → 점수
    evaluations = []
    for n, d in slides.items():
        if "sentences" not in d or n in ver_errors:
            continue
        apply_verification(d["sentences"], verified.get(n))
        results = aggregate_key_points(
            d["rubric"], d["sentences"], d["fact_checks"], d["items"], d["stt_norm"]
        )
        evaluation = SlideEvaluation(
            take_id=take.take_id,
            script_name=take.script_name,
            slide_number=n,
            rubric_id=d["rubric"].meta.rubric_id,
            scores=slide_scores(results, d["fact_checks"], d["fidelity"], d["items"]),
            sentences=d["sentences"],
            key_points=results,
            critical_facts=d["fact_checks"],
            similar_items=d["items"],
            fidelity=d["fidelity"],
            alignments=d["alignments"],
            verification={
                "required": n in ver_messages,
                "sentences": [s.sentence_index for s in d["sentences"] if s.conflicts],
                "conflicts": {
                    c: [s.sentence_index for s in d["sentences"] if c in s.conflicts]
                    for c in CONFLICT_TEXT
                    if any(c in s.conflicts for s in d["sentences"])
                },
            },
            stt_sentences=[s.text for s in d["stt_norm"].sentences],
            stt_text=d["stt_norm"].text,
            created_at=datetime.now(UTC).isoformat(timespec="seconds"),
        )
        evaluations.append(evaluation)

    failed = {n: f"의미 평가 {e}" for n, e in sem_errors.items()} | {
        n: f"교차 검증 {e}" for n, e in ver_errors.items()
    }
    stats = {
        "slides": len(slides),
        "semantic_calls": sem_calls,
        "verifier_calls": ver_calls,
        "verified_slides": len(ver_messages),
        "failed": failed,
    }
    return evaluations, stats
