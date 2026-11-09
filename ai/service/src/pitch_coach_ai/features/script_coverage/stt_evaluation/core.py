"""STT 평가 진입점: 발표 연습 한 번(STT)을 대본의 평가 기준으로 채점한다.

LLM 과 캐시는 인자로 받는다. 평가 기준(rubric)을 읽고 결과를 저장하는 일은 호출하는 쪽이 한다.
평가 기준이 없는 장은 채점하지 않고 `stats["failed"]` 에 `RUBRIC_MISSING` 으로 남긴다.
STT 가 빈 장은 LLM 의미 평가를 부르지 않고 코드가 모든 전달 단위를 missing 으로 판정한다.
"""

from ..shared.llm_step import LLMCache, llm_error_code, llm_step, record_failures
from ..shared.rubric import EvaluationRubric
from ..shared.text import normalize_script
from .align import align_sentences, fidelity_excluding, positioned_tokens
from .config import OFFSCRIPT_LLM_ENABLED
from .detail import annotate_expression, slide_detail
from .fact_check import annotate_mismatches, check_critical_facts
from .fallback import lexical_fallback
from .judge import (
    empty_stt_evaluation,
    eval_config_hash,
    evaluate_semantics,
    semantic_eval_message,
)
from .merge import (
    CONFLICT_TEXT,
    aggregate_key_points,
    merge_sentences,
    raised_by_name,
    resolve_name_checks,
)
from .normalize import normalize_stt
from .offscript import (
    OffscriptJudgment,
    apply_offscript_judgments,
    check_offscript,
    classify_offscript,
    offscript_config_hash,
    offscript_message,
)
from .schemas import SemanticEvaluation, SlideEvaluation, Take, VerifierResult
from .scoring import slide_scores
from .similar import find_similar_items, settle_facts
from .speech_mix import build_offscript, build_speech_mix, said_in_other_slides
from .verify import (
    apply_verification,
    check_verification,
    verifier_config_hash,
    verifier_message,
    verify,
)


def evaluate_take(
    take: Take,
    rubrics: dict[int, EvaluationRubric],
    *,
    eval_llm,
    verifier_llm,
    model: str,
    cache: LLMCache | None = None,
    max_workers: int = 4,
    integrity: dict[int, str] | None = None,
    offscript_llm=None,
) -> tuple[list[SlideEvaluation], dict]:
    """Presentation Evaluation Pipeline. 발표 연습 한 번(STT)을 대본의 평가 기준으로 채점한다.

    ① STT 정규화 → ② 규칙 분석 (문장 정렬 · 핵심 사실 검증 · 비슷한 말 찾기 · 대본 충실도) ∥ LLM 의미 평가 (대본 문장별, 슬라이드당 1회)
    → ③ 문장별 병합·충돌 검사 (코드) → ④ 충돌한 문장이 있는 슬라이드만 LLM 교차 검증 (슬라이드당 1회)
    → ⑤ Key Point 판정 = 문장 판정 모으기 (코드) → 점수 계산 (코드).
    `rubrics` = {슬라이드 번호: 평가 기준}. 대본을 다시 분석하지 않고 주어진 평가 기준을 읽어 쓴다.
    결과(평가 목록)는 저장하지 않고 돌려준다 — 저장은 호출하는 쪽이 한다.
    LLM 호출이 실패한 슬라이드는 평가를 만들지 않고 stats["failed"] 에 {슬라이드 번호: 에러 코드}로,
    stats["causes"] 에 {슬라이드 번호: "<단계> <예외 클래스 이름>"}(로그용)으로 남긴다.
    그 장의 간이 값(`lexical_fallback`)은 stats["fallbacks"] 에 {슬라이드 번호: Fallback} 으로 남긴다.
    `integrity` = {슬라이드 번호: STT 완전성} (없으면 모두 `unknown`). 채점한 장에는 장 상세(`detail`)와
    대본 밖 구간(`offscript`)이 붙는다. `rubrics` 에는 채점하지 않는 다른 장의 평가 기준도 넣을 수 있다
    (다른 장 내용을 알아보는 데만 쓴다).
    """
    integrity = integrity or {}
    slides = {}
    failed: dict[int, str] = {}
    causes: dict[int, str] = {}
    for s in take.slides:
        rubric = rubrics.get(s.slide_number)
        if rubric is None:  # 나머지 장은 계속 채점한다
            failed[s.slide_number] = "RUBRIC_MISSING"
            causes[s.slide_number] = "평가 기준 없음"
            continue
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
        # 대본 밖 분류(LLM) 자리 — OFFSCRIPT_LLM_ENABLED
        offscript = build_offscript(
            rubric,
            stt_norm,
            stt_ptokens,
            alignments,
            {n: r for n, r in rubrics.items() if n != s.slide_number},
        )
        speech_mix = build_speech_mix(offscript, stt_ptokens, s.stt)
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
            offscript=offscript,
            speech_mix=speech_mix,
            raw_stt=s.stt,
        )

    # ② LLM 의미 평가 (규칙 분석과 독립: 규칙 결과를 입력에 넣지 않는다)
    # STT 가 빈 장은 LLM 을 부르지 않고 코드가 판정한다 (캐시에 넣지 않고, 호출 수에도 세지 않는다)
    empty = {n for n, d in slides.items() if not d["stt_norm"].text}
    messages = {
        n: semantic_eval_message(n, d["rubric"], d["stt_norm"], d["unverified"])
        for n, d in slides.items()
        if n not in empty
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

    for n in empty:
        semantics[n] = empty_stt_evaluation(slides[n]["rubric"], slides[n]["unverified"])

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
        # 검증 대상을 다 덮지 못한 출력은 잘못된 출력이다 — 그 장은 1차 판정만으로 내지 않는다
        check=lambda n, result: check_verification(slides[n]["sentences"], result),
    )

    # ④-1 대본 밖 분류 (LLM, OFFSCRIPT_LLM_ENABLED 이고 LLM 이 있을 때만). 핵심 채점(의미 평가 · 교차 검증)
    # 뒤에 돌려 그 호출이 먼저 자원을 쓰게 한다. 실패 · 마감이면 그 장은 규칙 값을 그대로 두고 이유만 붙인다
    # (장 채점을 실패로 하지 않는다)
    off_calls = 0
    if OFFSCRIPT_LLM_ENABLED and offscript_llm is not None:
        off_messages = {}
        for n, d in slides.items():
            if "sentences" not in d or n in ver_errors:
                continue
            message = offscript_message(
                d["rubric"], d["offscript"], [s.text for s in d["stt_norm"].sentences]
            )
            if message is not None:
                off_messages[n] = message
        judged, off_errors, off_calls = llm_step(
            "stt.offscript",
            off_messages,
            classify_offscript,
            offscript_llm,
            offscript_config_hash(model),
            OffscriptJudgment,
            cache=cache,
            max_workers=min(2, max_workers),
            check=lambda n, result: check_offscript(slides[n]["offscript"], result),
        )
        for n, result in judged.items():
            if result is None:
                continue
            d = slides[n]
            d["offscript"] = apply_offscript_judgments(d["offscript"], result)
            d["speech_mix"] = build_speech_mix(d["offscript"], d["stt_ptokens"], d["raw_stt"])
        for n, error in off_errors.items():
            d = slides[n]
            reason = (
                "OFFSCRIPT_DEADLINE"
                if llm_error_code(error) == "LLM_TIMEOUT"
                else "OFFSCRIPT_FAILED"
            )
            d["offscript"].reason_codes.append(reason)
            d["speech_mix"].reason_codes.append(reason)

    # ⑤ Key Point 판정(문장 판정 모으기) → 점수
    evaluations = []
    for n, d in slides.items():
        if "sentences" not in d or n in ver_errors:
            continue
        apply_verification(
            d["sentences"],
            verified.get(n),
            raised_by_name(d["sentences"], d["fact_checks"]),
        )
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
            stt_integrity=integrity.get(n, "unknown"),
            offscript=d["offscript"],
        )
        annotate_expression(evaluation.sentences)
        evaluations.append(evaluation)

    # 장 상세: 다른 장에서 말한 핵심 내용은 모든 장을 만든 뒤에 알 수 있다
    said = said_in_other_slides(
        {e.slide_number: (e.offscript, e.stt_sentences) for e in evaluations}, rubrics
    )
    for e in evaluations:
        d = slides[e.slide_number]
        e.detail = slide_detail(
            e,
            d["rubric"],
            stt_integrity=e.stt_integrity,
            speech_mix=d["speech_mix"],
            said={kp: nums for (n, kp), nums in said.items() if n == e.slide_number},
        )

    record_failures(failed, causes, sem_errors, "의미 평가")
    record_failures(failed, causes, ver_errors, "교차 검증")
    fallbacks = {
        n: lexical_fallback(
            slides[n]["rubric"],
            slides[n]["alignments"],
            slides[n]["fact_checks"],
            slides[n]["fidelity"],
            stt_integrity=integrity.get(n, "unknown"),
            speech_mix=slides[n]["speech_mix"],
            reason_code=code,
        )
        for n, code in sorted(failed.items())
        if n in slides  # 평가 기준이 없는 장(RUBRIC_MISSING)은 최상위에서 만든다
    }
    stats = {
        "slides": len(slides),
        "semantic_calls": sem_calls,
        "verifier_calls": ver_calls,
        "verified_slides": len(ver_messages),
        "offscript_calls": off_calls,
        "failed": dict(sorted(failed.items())),
        "causes": dict(sorted(causes.items())),
        "fallbacks": fallbacks,
    }
    return evaluations, stats
