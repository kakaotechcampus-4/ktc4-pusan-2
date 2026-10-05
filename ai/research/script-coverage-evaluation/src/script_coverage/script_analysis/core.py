"""대본 분석 파이프라인: 대본(슬라이드 목록) → 슬라이드별 평가 기준(EvaluationRubric).

슬라이드마다 ① 규칙 기반 분석 1회 ∥ LLM 1차 분석 1회 → ② 1차 결과 정리·검증(코드)
→ ③ LLM 최종 결론 1회 → ④ 조립(코드) → ⑤ LLM 전달 단위 1회 → 검증(코드).
LLM 호출이 실패한 슬라이드는 평가 기준을 만들지 않고 stats["failed"] 에 남긴다.
성공한 호출은 캐시에 남으므로 다시 돌리면 실패한 슬라이드만 호출한다.
평가 기준을 저장하는 일은 호출한 쪽이 한다.
"""

from concurrent.futures import ThreadPoolExecutor

from ..shared.facts import extract_critical_facts
from ..shared.llm_step import LLMCache, cache_get, cache_put, call_in_parallel, message_hash
from ..shared.rubric import EvaluationRubric
from ..shared.text import normalize_script
from .draft import draft_rubric
from .final import (
    final_config_hash,
    final_user_message,
    finalize_rubric,
    name_candidates,
    review_final,
)
from .keywords import extract_keywords_tfidf
from .schemas import FinalReview, SlideScript, SlideSemanticAnalysis, SlideUnits
from .semantic import EMPTY_SEMANTICS, analyze_semantics, semantic_config_hash
from .units import attach_units, extract_units, unit_config_hash, unit_user_message


def analyze_script(
    script_name: str,
    slides: list[SlideScript],
    *,
    semantic_llm,
    final_llm,
    unit_llm,
    model: str,
    cache: LLMCache | None = None,
    max_workers: int = 4,
) -> tuple[list[EvaluationRubric], dict]:
    """Script Analysis Pipeline. 대본 등록·수정 시 한 번 실행한다.

    입력은 발표 하나의 슬라이드 대본 목록이고, `script_name` 은 발표를 가리키는 이름이다.
    LLM 은 출력 스키마가 고정된 것을 받는다 (`semantic_llm`: SlideSemanticAnalysis,
    `final_llm`: FinalReview, `unit_llm`: SlideUnits). `model` 은 설정 해시와 평가 기준 메타에 들어간다.
    슬라이드는 받은 순서와 상관없이 번호순으로 분석한다.
    """
    slides = sorted(slides, key=lambda s: s.slide_number)
    norms = [normalize_script(s.script) for s in slides]
    deck_texts = [n.text for n in norms]
    keywords = extract_keywords_tfidf(norms)
    failed: dict[int, str] = {}
    semantic_config = semantic_config_hash(model)
    final_config = final_config_hash(model)
    unit_config = unit_config_hash(model)

    # ① 규칙 기반 분석 ∥ LLM 1차 분석 (캐시에 없고 읽을 내용이 있는 슬라이드만 호출)
    semantics = {
        i: cache_get(
            cache, "script.semantic", n.content_hash, semantic_config, SlideSemanticAnalysis
        )
        for i, n in enumerate(norms)
    }
    analysis_jobs = {
        i: (slides[i], norms[i], semantic_llm)
        for i in semantics
        if semantics[i] is None and norms[i].text
    }
    with ThreadPoolExecutor(
        max_workers=1
    ) as waiter:  # LLM 호출을 기다리는 동안 메인 스레드는 규칙 분석을 한다
        pending = waiter.submit(call_in_parallel, analysis_jobs, analyze_semantics, max_workers)
        facts = [extract_critical_facts(n) for n in norms]
        analyzed, analysis_errors = pending.result()
    for i, sem in analyzed.items():
        semantics[i] = sem
        cache_put(
            cache, "script.semantic", norms[i].content_hash, semantic_config, sem
        )  # 캐시는 메인 스레드에서만
    failed.update({slides[i].slide_number: f"1차 분석 {msg}" for i, msg in analysis_errors.items()})

    # ② 1차 결과 정리·검증 (코드)
    ready = [i for i in range(len(slides)) if i not in analysis_errors]
    drafts = {
        i: draft_rubric(
            script_name, slides[i], norms[i], facts[i], keywords[i],
            semantics[i] or EMPTY_SEMANTICS, deck_texts, model,
        )
        for i in ready
    }  # fmt: skip

    # ③ LLM 최종 결론 (읽을 내용이 있는 슬라이드만)
    inputs = {}
    for i in ready:
        if norms[i].text:
            candidates = name_candidates(drafts[i], facts[i], norms[i], deck_texts)
            inputs[i] = (
                candidates,
                final_user_message(
                    slides[i], norms[i], drafts[i], facts[i], candidates, deck_texts
                ),
            )
    reviews = {
        i: cache_get(cache, "script.final", message_hash(message), final_config, FinalReview)
        for i, (_, message) in inputs.items()
    }
    final_jobs = {i: (inputs[i][1], final_llm) for i in reviews if reviews[i] is None}
    reviewed, final_errors = call_in_parallel(final_jobs, review_final, max_workers)
    for i, review in reviewed.items():
        reviews[i] = review
        cache_put(cache, "script.final", message_hash(inputs[i][1]), final_config, review)
    failed.update({slides[i].slide_number: f"최종 결론 {msg}" for i, msg in final_errors.items()})

    # ④ 조립(코드)
    assembled = {}
    for i in ready:
        if i in final_errors:
            continue
        if i not in inputs:  # 읽을 내용이 없는 슬라이드는 1차 결과가 곧 최종이다
            assembled[i] = drafts[i]
        else:
            assembled[i] = finalize_rubric(
                script_name, slides[i], norms[i], facts[i], keywords[i],
                drafts[i], reviews[i], inputs[i][0], deck_texts, model,
            )  # fmt: skip

    # ⑤ LLM 전달 단위 (나눌 문장이 있는 슬라이드만) → 검증(코드)
    unit_inputs = {
        i: unit_user_message(slides[i], r)
        for i, r in assembled.items()
        if any(x != "skip" for x in r.sentence_roles)
    }
    units = {
        i: cache_get(cache, "script.units", message_hash(message), unit_config, SlideUnits)
        for i, message in unit_inputs.items()
    }
    unit_jobs = {i: (unit_inputs[i], unit_llm) for i in units if units[i] is None}
    extracted, unit_errors = call_in_parallel(unit_jobs, extract_units, max_workers)
    for i, output in extracted.items():
        units[i] = output
        cache_put(cache, "script.units", message_hash(unit_inputs[i]), unit_config, output)
    failed.update({slides[i].slide_number: f"전달 단위 {msg}" for i, msg in unit_errors.items()})

    rubrics = []
    for i, rubric in assembled.items():
        if i in unit_errors:
            continue
        rubric = attach_units(rubric, units.get(i), model)
        rubrics.append(rubric)

    stats = {
        "slides": len(slides),
        "analysis_calls": len(analysis_jobs),
        "final_calls": len(final_jobs),
        "unit_calls": len(unit_jobs),
        "failed": failed,
    }
    return rubrics, stats
