"""대본 분석 파이프라인: 대본(슬라이드 목록) → 슬라이드별 평가 기준(EvaluationRubric).

슬라이드마다 ① 규칙 기반 분석 1회 ∥ LLM 1차 분석 1회 → ② 1차 결과 정리·검증(코드)
→ ③ LLM 최종 결론 1회 → ④ 조립(코드) → ⑤ LLM 전달 단위 1회 → 검증(코드).
슬라이드는 서로 기다리지 않는다: 한 슬라이드의 LLM 단계가 끝나면 그 슬라이드의 다음 단계를 바로 넣는다
(단계마다 모든 슬라이드를 기다리면 느린 슬라이드 하나가 다음 단계 전체를 늦춘다). 동시에 도는 LLM 호출은
`max_workers` 개까지이고, 빈자리가 나면 이미 시작한 슬라이드의 다음 단계(전달 단위 → 최종 결론)를 새 슬라이드의
1차 분석보다 먼저 넣는다 — 마감 안에 끝나는 슬라이드를 늘리기 위해서다. LLM 호출만 다른 스레드에서 돌고,
캐시 읽기 · 쓰기와 코드 단계(초안 · 조립 · 검증)는 모두 부르는 스레드에서 한다.
LLM 호출이 실패한 슬라이드는 평가 기준을 만들지 않고 stats["failed"] 에 {슬라이드 번호: 에러 코드}로,
stats["causes"] 에 {슬라이드 번호: "<단계> <예외 클래스 이름>"}(로그용)으로 남긴다.
성공한 호출은 캐시에 남으므로 다시 돌리면 실패한 슬라이드만 호출한다.
`targets` 를 주면 그 슬라이드만 LLM 으로 분석하고 평가 기준을 만든다 (정규화 · 키워드 · 이름 판정의
맥락은 받은 모든 슬라이드로 낸다). 평가 기준을 저장하는 일은 호출한 쪽이 한다.
"""

from collections import deque
from collections.abc import Collection
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait

from ..shared.facts import extract_critical_facts
from ..shared.llm_step import LLMCache, cache_get, cache_put, message_hash, record_failures
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

# 단계 이름 (실패 원인 기록에 쓴다) · 빈자리가 났을 때 먼저 넣는 순서 (뒤 단계부터)
STAGE_NAMES = {"semantic": "1차 분석", "final": "최종 결론", "unit": "전달 단위"}
STAGE_PRIORITY = ("unit", "final", "semantic")


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
    targets: Collection[int] | None = None,
) -> tuple[list[EvaluationRubric], dict]:
    """Script Analysis Pipeline. 대본 등록·수정 시 한 번 실행한다.

    입력은 발표 하나의 슬라이드 대본 목록이고, `script_name` 은 발표를 가리키는 이름이다.
    LLM 은 출력 스키마가 고정된 것을 받는다 (`semantic_llm`: SlideSemanticAnalysis,
    `final_llm`: FinalReview, `unit_llm`: SlideUnits). `model` 은 설정 해시와 평가 기준 메타에 들어간다.
    슬라이드는 받은 순서와 상관없이 번호순으로 분석하고, 결과도 번호순이다.
    `targets` = 평가 기준을 만들 슬라이드 번호 (None 이면 전부). 대상이 아닌 슬라이드는 LLM 을 부르지 않고
    결과에도 없지만, 키워드(TF-IDF)와 발표 공통 어휘 · 이름 판정에는 맥락으로 쓰인다.
    `max_workers` = 동시에 도는 LLM 호출 수 (단계와 상관없이 합쳐서).
    """
    slides = sorted(slides, key=lambda s: s.slide_number)
    norms = [normalize_script(s.script) for s in slides]
    deck_texts = [n.text for n in norms]
    keywords = extract_keywords_tfidf(norms)
    chosen = [i for i, s in enumerate(slides) if targets is None or s.slide_number in targets]
    configs = {
        "semantic": semantic_config_hash(model),
        "final": final_config_hash(model),
        "unit": unit_config_hash(model),
    }
    calls = {"semantic": 0, "final": 0, "unit": 0}
    errors: dict[str, dict[int, BaseException]] = {"semantic": {}, "final": {}, "unit": {}}
    queues: dict[str, deque] = {stage: deque() for stage in STAGE_PRIORITY}  # (슬라이드, 인자)
    facts: dict[int, object] = {}
    drafts: dict[int, EvaluationRubric] = {}
    inputs: dict[int, tuple] = {}  # 최종 결론의 (이름 후보, 메시지)
    unit_inputs: dict[int, str] = {}
    rubrics: dict[int, EvaluationRubric] = {}

    def after_units(i: int, output: SlideUnits | None) -> None:
        rubrics[i] = attach_units(rubrics[i], output, model)

    def after_final(i: int, review: FinalReview | None) -> None:
        """④ 조립(코드) → ⑤ 전달 단위 (나눌 문장이 있는 슬라이드만)."""
        if review is None:  # 읽을 내용이 없는 슬라이드는 1차 결과가 곧 최종이다
            rubric = drafts[i]
        else:
            rubric = finalize_rubric(
                script_name, slides[i], norms[i], facts[i], keywords[i],
                drafts[i], review, inputs[i][0], deck_texts, model,
            )  # fmt: skip
        rubrics[i] = rubric
        if not any(x != "skip" for x in rubric.sentence_roles):
            after_units(i, None)
            return
        unit_inputs[i] = unit_user_message(slides[i], rubric)
        cached = cache_get(
            cache, "script.units", message_hash(unit_inputs[i]), configs["unit"], SlideUnits
        )
        if cached is not None:
            after_units(i, cached)
        else:
            queues["unit"].append((i, (unit_inputs[i], unit_llm)))

    def after_semantic(i: int, semantics: SlideSemanticAnalysis | None) -> None:
        """② 1차 결과 정리 · 검증(코드) → ③ 최종 결론 (읽을 내용이 있는 슬라이드만)."""
        drafts[i] = draft_rubric(
            script_name, slides[i], norms[i], facts[i], keywords[i],
            semantics or EMPTY_SEMANTICS, deck_texts, model,
        )  # fmt: skip
        if not norms[i].text:
            after_final(i, None)
            return
        candidates = name_candidates(drafts[i], facts[i], norms[i], deck_texts)
        message = final_user_message(
            slides[i], norms[i], drafts[i], facts[i], candidates, deck_texts
        )
        inputs[i] = (candidates, message)
        cached = cache_get(
            cache, "script.final", message_hash(message), configs["final"], FinalReview
        )
        if cached is not None:
            after_final(i, cached)
        else:
            queues["final"].append((i, (message, final_llm)))

    def finished(stage: str, i: int, output) -> None:
        """LLM 한 단계가 끝난 슬라이드: 캐시에 남기고 (부르는 스레드에서만) 다음 단계로 넘긴다."""
        if stage == "semantic":
            cache_put(cache, "script.semantic", norms[i].content_hash, configs["semantic"], output)
            after_semantic(i, output)
        elif stage == "final":
            cache_put(cache, "script.final", message_hash(inputs[i][1]), configs["final"], output)
            after_final(i, output)
        else:
            cache_put(cache, "script.units", message_hash(unit_inputs[i]), configs["unit"], output)
            after_units(i, output)

    # ① 1차 분석: 캐시에 없고 읽을 내용이 있는 슬라이드만 부른다
    semantic_ready = []
    for i in chosen:
        cached = cache_get(
            cache,
            "script.semantic",
            norms[i].content_hash,
            configs["semantic"],
            SlideSemanticAnalysis,
        )
        if cached is None and norms[i].text:
            queues["semantic"].append((i, (slides[i], norms[i], semantic_llm)))
        else:
            semantic_ready.append((i, cached))

    functions = {"semantic": analyze_semantics, "final": review_final, "unit": extract_units}
    running: dict[Future, tuple[str, int]] = {}
    workers = max(1, max_workers)
    with ThreadPoolExecutor(max_workers=workers) as pool:

        def fill() -> None:
            while len(running) < workers and any(queues.values()):
                stage = next(s for s in STAGE_PRIORITY if queues[s])
                i, args = queues[stage].popleft()
                calls[stage] += 1
                running[pool.submit(functions[stage], *args)] = (stage, i)

        fill()  # LLM 1차 분석을 기다리는 동안 부르는 스레드는 규칙 분석을 한다
        facts.update({i: extract_critical_facts(norms[i]) for i in chosen})
        for i, cached in semantic_ready:
            after_semantic(i, cached)
        fill()
        while running:
            done, _ = wait(running, return_when=FIRST_COMPLETED)
            for future in sorted(
                done, key=lambda f: running[f][1]
            ):  # 같이 끝나면 슬라이드 순서대로
                stage, i = running.pop(future)
                try:
                    output = future.result()
                except Exception as e:  # 한 장이 실패해도 나머지 (이미 비용을 낸) 결과는 살린다
                    errors[stage][i] = e
                else:
                    finished(stage, i, output)
            fill()

    failed: dict[int, str] = {}
    causes: dict[int, str] = {}
    for stage in ("semantic", "final", "unit"):
        record_failures(
            failed,
            causes,
            {slides[i].slide_number: e for i, e in errors[stage].items()},
            STAGE_NAMES[stage],
        )
    lost = {i for stage_errors in errors.values() for i in stage_errors}
    stats = {
        "slides": len(chosen),
        "analysis_calls": calls["semantic"],
        "final_calls": calls["final"],
        "unit_calls": calls["unit"],
        "failed": dict(sorted(failed.items())),
        "causes": dict(sorted(causes.items())),
    }
    # 전달 단위 단계에서 실패한 슬라이드는 조립까지 됐어도 내지 않는다
    return [rubrics[i] for i in sorted(rubrics) if i not in lost], stats
