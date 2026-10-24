"""최상위 진입 함수(`script_coverage.core`)로 파이프라인을 돌리고 결과를 DB 에 저장하는 실행 함수.

서비스가 부르는 것과 같은 진입 함수(`analyze_script` · `evaluate_take`)를 거치므로 연구에서 잰 값이
서비스 응답과 같다. 최상위 응답에는 LLM 호출 수가 없어서, 실험 도구가 LLM 객체를 호출 수를 세는 감싸개에 넣어 센다.
"""

import sqlite3
from pathlib import Path

from script_coverage.core import (
    CoverageError,
    ScriptLLMs,
    TakeLLMs,
    analyze_script,
    evaluate_take,
)
from script_coverage.schemas import AnalyzeRequest, RubricRequest, ScriptSlide, TakeSlide
from script_coverage.shared.llm_step import LLMCache
from script_coverage.shared.rubric import EvaluationRubric
from script_coverage.stt_evaluation.schemas import SlideEvaluation, Take

from .cache import SampleCache, SqliteLLMCache
from .datasets import load_script_json
from .llm import Settings, script_llms, stt_llms
from .store import delete_rubric, load_rubrics, save_evaluation, save_rubric


class _CountingLLM:
    """`.invoke` 를 부른 횟수를 세어 넘기는 감싸개. 캐시에 있어 LLM 을 부르지 않은 호출은 세지 않는다."""

    def __init__(self, inner, counts: dict[str, int], key: str):
        self._inner, self._counts, self._key = inner, counts, key

    def invoke(self, messages):
        self._counts[self._key] += 1  # 동시에 부를 수 있어도 파이썬의 += 는 GIL 아래서 충분하다
        return self._inner.invoke(messages)


def _failure_stats(response_failed, calls: dict[str, int]) -> dict:
    """응답 `failed` → 실험이 쓰는 stats: `failed`({장 번호: 에러 코드}) · `causes`({장 번호: 고정 문구}) + 호출 수."""
    return {
        "failed": {f.slide_number: f.error.code for f in response_failed},
        "causes": {f.slide_number: f.error.message for f in response_failed},
        **calls,
    }


def run_script_analysis(
    path: Path,
    conn: sqlite3.Connection,
    settings: Settings,
    *,
    cache: LLMCache | None = None,
    llms: ScriptLLMs | None = None,
) -> tuple[list[EvaluationRubric], dict]:
    """대본 JSON 하나(= 발표 하나)를 분석해 평가 기준을 DB 에 저장한다. 결과는 파일 이름(확장자 제외)으로 저장한다.

    `script_coverage.core.analyze_script` 를 대본 전체(`slide_numbers=None`)로 부른다. 대본 식별자
    (`pitch_id` · `script_version_id`)는 파일 이름이다.
    캐시는 기본으로 DB 의 캐시 표를 쓴다 (같은 대본 · 같은 LLM 설정이면 API 를 다시 부르지 않는다).
    LLM 호출이 실패한 슬라이드는 새로 저장하지 않고 `stats["failed"]`(에러 코드) · `stats["causes"]`(문구)에 남긴다.
    그 슬라이드의 예전 평가 기준이 DB 에 있으면 **지운다** — 고치기 전 대본의 기준이 남아 STT 평가가 그것으로
    채점하는 일을 막기 위해서다 (다시 돌리면 그 슬라이드만 호출한다). 모든 슬라이드가 실패해 `CoverageError` 가
    나오면 모든 슬라이드의 예전 평가 기준을 지우고 그 예외를 그대로 올린다.
    `stats` 에는 이번 실행에서 실제로 LLM 을 부른 횟수 `analysis_calls` · `final_calls` · `unit_calls` 도 있다.
    """
    cache = cache if cache is not None else SqliteLLMCache(conn)
    llms = llms if llms is not None else script_llms(settings)
    slides = load_script_json(path)
    counts = {"analysis_calls": 0, "final_calls": 0, "unit_calls": 0}
    counted = ScriptLLMs(
        semantic=_CountingLLM(llms.semantic, counts, "analysis_calls"),
        final=_CountingLLM(llms.final, counts, "final_calls"),
        unit=_CountingLLM(llms.unit, counts, "unit_calls"),
    )
    request = RubricRequest(
        pitch_id=path.stem,
        script_version_id=path.stem,
        slides=[ScriptSlide(slide_number=s.slide_number, script=s.script) for s in slides],
    )
    try:
        response = analyze_script(request, llms=counted, model=settings.model, cache=cache)
    except CoverageError:
        for slide in slides:
            delete_rubric(conn, path.stem, slide.slide_number)
        raise
    stats = _failure_stats(response.failed, counts)
    for slide_number in stats["failed"]:
        delete_rubric(conn, path.stem, slide_number)
    for rubric in response.rubrics:
        save_rubric(conn, rubric)
    return response.rubrics, stats


def run_take_evaluation(
    take: Take,
    conn: sqlite3.Connection,
    settings: Settings,
    *,
    sample: int = 0,
    cache: LLMCache | None = None,
    llms: TakeLLMs | None = None,
) -> tuple[list[SlideEvaluation], dict]:
    """연습 한 번(STT)을 DB 의 평가 기준으로 채점한다. 평가 기준이 없는 슬라이드는 `stats["failed"]` 에 `RUBRIC_MISSING` 으로 남는다.

    `script_coverage.core.evaluate_take` 를 부른다 (평가 기준 = DB 에 저장된 그 대본의 것). 모든 슬라이드의 평가 기준이
    없으면 예외 없이 빈 결과와 `RUBRIC_MISSING` 목록을 돌려주고 (실험이 "01 을 먼저 실행" 안내를 낸다),
    LLM 오류로 모두 실패하면 `CoverageError` 를 그대로 올린다.
    `sample = k > 0` 이면 같은 입력을 k 번째로 다시 채점한다 (반복 채점). 캐시를 따로 쓰고 결과는 저장하지 않는다.
    `stats` 에는 이번 실행에서 실제로 LLM 을 부른 횟수 `semantic_calls` · `verifier_calls` 도 있다.
    """
    base = cache if cache is not None else SqliteLLMCache(conn)
    llms = llms if llms is not None else stt_llms(settings)
    counts = {"semantic_calls": 0, "verifier_calls": 0}
    counted = TakeLLMs(
        eval=_CountingLLM(llms.eval, counts, "semantic_calls"),
        verifier=_CountingLLM(llms.verifier, counts, "verifier_calls"),
    )
    request = AnalyzeRequest(
        take_id=take.take_id,
        slides=[TakeSlide(slide_number=s.slide_number, stt=s.stt) for s in take.slides],
        rubrics=list(load_rubrics(conn, take.script_name).values()),
    )
    try:
        response = evaluate_take(
            request, llms=counted, model=settings.model, cache=SampleCache(base, sample)
        )
    except CoverageError as error:
        if error.code != "RUBRIC_MISSING":
            raise
        missing = {s.slide_number: "RUBRIC_MISSING" for s in take.slides}
        return [], {"failed": missing, "causes": {n: error.message for n in missing}, **counts}
    if not sample:
        for evaluation in response.evaluations:
            save_evaluation(conn, evaluation)
    return response.evaluations, _failure_stats(response.failed, counts)
