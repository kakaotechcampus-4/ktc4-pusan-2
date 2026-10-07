"""코어 파이프라인을 돌리고 결과를 저장하는 실행 함수 (노트북의 `run_script_analysis` · `evaluate_take` 와 같은 일)."""

import sqlite3
from pathlib import Path

from script_coverage.script_analysis.core import analyze_script
from script_coverage.shared.llm_step import LLMCache
from script_coverage.shared.rubric import EvaluationRubric
from script_coverage.stt_evaluation.core import evaluate_take
from script_coverage.stt_evaluation.schemas import SlideEvaluation, Take

from .cache import SampleCache, SqliteLLMCache
from .datasets import load_script_json
from .llm import Settings, script_llms, stt_llms
from .store import delete_rubric, load_rubrics, save_evaluation, save_rubric


def run_script_analysis(
    path: Path,
    conn: sqlite3.Connection,
    settings: Settings,
    *,
    cache: LLMCache | None = None,
    llms: dict | None = None,
    max_workers: int = 4,
) -> tuple[list[EvaluationRubric], dict]:
    """대본 JSON 하나(= 발표 하나)를 분석해 평가 기준을 DB 에 저장한다. 결과는 파일 이름(확장자 제외)으로 저장한다.

    캐시는 기본으로 DB 의 캐시 표를 쓴다 (같은 대본 · 같은 LLM 설정이면 API 를 다시 부르지 않는다).
    LLM 호출이 실패한 슬라이드는 새로 저장하지 않고 `stats["failed"]` 에 남긴다. 그 슬라이드의 예전 평가 기준이 DB 에
    있으면 **지운다** — 고치기 전 대본의 기준이 남아 STT 평가가 그것으로 채점하는 일을 막기 위해서다 (다시 돌리면 그 슬라이드만 호출한다).
    """
    cache = cache if cache is not None else SqliteLLMCache(conn)
    llms = llms if llms is not None else script_llms(settings)
    rubrics, stats = analyze_script(
        path.stem,
        load_script_json(path),
        **llms,
        model=settings.model,
        cache=cache,
        max_workers=max_workers,
    )
    for slide_number in stats["failed"]:
        delete_rubric(conn, path.stem, slide_number)
    for rubric in rubrics:
        save_rubric(conn, rubric)
    return rubrics, stats


def run_take_evaluation(
    take: Take,
    conn: sqlite3.Connection,
    settings: Settings,
    *,
    sample: int = 0,
    cache: LLMCache | None = None,
    llms: dict | None = None,
    max_workers: int = 4,
) -> tuple[list[SlideEvaluation], dict]:
    """연습 한 번(STT)을 DB 의 평가 기준으로 채점한다. 평가 기준이 없으면 `ValueError`.

    `sample = k > 0` 이면 같은 입력을 k 번째로 다시 채점한다 (반복 채점). 캐시를 따로 쓰고 결과는 저장하지 않는다.
    """
    base = cache if cache is not None else SqliteLLMCache(conn)
    llms = llms if llms is not None else stt_llms(settings)
    evaluations, stats = evaluate_take(
        take,
        load_rubrics(conn, take.script_name),
        **llms,
        model=settings.model,
        cache=SampleCache(base, sample),
        max_workers=max_workers,
    )
    if not sample:
        for evaluation in evaluations:
            save_evaluation(conn, evaluation)
    return evaluations, stats
