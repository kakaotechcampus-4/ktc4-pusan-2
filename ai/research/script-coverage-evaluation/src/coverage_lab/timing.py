"""장 수별 소요 시간 측정(`experiments/07_timing.py`)의 덱 만들기와 요청 하나의 기록 (LLM 없이 시험할 수 있는 부분).

- 덱: 가상대본들의 장을 차례로 이어 붙여 N장을 만든다. 풀보다 길면 앞에서부터 다시 쓰고 장 번호는 1..N 으로 새로 매긴다.
  각 장에는 같은 원래 장의 STT(`<대본>_take1`)를 붙인다
- 기록: 걸린 시간 · 대상 장 수 · 실패 장(요청 전체가 실패하면 대상 장 모두) · 실제로 결과가 나온 장 수 · 호출 기록
- BE 재시도: 최초 200 의 LLM 오류 장만 `slide_numbers` 로 1번 (`retry_targets`), 요청 전체 실패면 전체 1번
"""

from dataclasses import dataclass

from pitch_coach_ai.features.script_coverage.schemas import ScriptSlide, TakeSlide
from pitch_coach_ai.features.script_coverage.shared.llm_step import LLM_ERROR_CODES

from .datasets import load_script_json, load_take, script_files, take_files


@dataclass(frozen=True)
class PoolSlide:
    script_name: str
    slide_number: int  # 원래 장 번호
    script: str
    stt: str


def slide_pool() -> list[PoolSlide]:
    """가상대본 파일 순서 · 장 번호 순서로 늘어놓은 장. 각 장에는 그 대본 take1 의 같은 장 STT 를 붙인다."""
    stt: dict[tuple[str, int], str] = {}
    for path in take_files():
        take = load_take(path)
        if path.stem == f"{take.script_name}_take1":
            stt.update({(take.script_name, s.slide_number): s.stt for s in take.slides})
    return [
        PoolSlide(path.stem, s.slide_number, s.script, stt[(path.stem, s.slide_number)])
        for path in script_files()
        for s in sorted(load_script_json(path), key=lambda s: s.slide_number)
    ]


def build_deck(pool: list[PoolSlide], n: int) -> tuple[list[ScriptSlide], list[TakeSlide]]:
    """N장 덱의 (대본 장, STT 장). 장 번호는 1..N, i번째 장은 풀의 i % len(pool) 번째 장이다."""
    picked = [pool[i % len(pool)] for i in range(n)]
    return (
        [ScriptSlide(slide_number=i + 1, script=p.script) for i, p in enumerate(picked)],
        [TakeSlide(slide_number=i + 1, stt=p.stt) for i, p in enumerate(picked)],
    )


def request_row(
    *,
    seconds: float,
    deadline_seconds: float,
    targets: int,
    failed: dict[int, str],
    produced: int,
    request_error: str | None,
    log_summary: dict,
) -> dict:
    """요청 하나의 기록. 요청 전체가 실패했으면(`request_error`) 대상 장을 모두 실패 장으로 센다 (장별 코드는 모른다)."""
    failed_slides = targets if request_error else len(failed)
    return {
        "seconds": seconds,
        "within_deadline": seconds <= deadline_seconds and failed_slides == 0,
        "target_slides": targets,
        "produced_slides": produced,
        "failed_slides": failed_slides,
        "failed": {str(n): code for n, code in sorted(failed.items())},
        "request_error": request_error,
        **log_summary,
    }


def retry_targets(failed: dict[int, str]) -> list[int]:
    """BE 가 최초 200 뒤 1번 다시 부를 장: LLM 에러 코드로 실패한 장만 (기능 문서 8절). 입력 오류 장은 빼고 번호순."""
    return sorted(n for n, code in failed.items() if code in LLM_ERROR_CODES)


def rubric_with_retry(request, all_slides: list[int], *, retry: bool) -> dict:
    """평가 기준 만들기 한 작업: 최초 요청 (+ BE 재시도 1번). 최종으로 만든 장 수를 센다.

    `request(slide_numbers)` 는 (기록, 응답 또는 None) 을 돌려준다 (None = 요청 전체 실패, 장별 코드 없음).
    재시도는 최초 응답이 있으면 LLM 오류 장만(`retry_targets`), 요청 전체가 실패했으면 모든 장을 대상으로 1번이다.
    재시도가 실패해도 최초에 만든 장은 그대로 센다.
    """
    first, response = request(None)
    rubrics = list(response.rubrics) if response is not None else []
    retry_row = None
    if retry:
        targets = (
            retry_targets({f.slide_number: f.error.code for f in response.failed})
            if response is not None
            else list(all_slides)
        )
        if targets:
            retry_row, again = request(targets)
            rubrics += list(again.rubrics) if again is not None else []
    return {
        "first": first,
        "retry": retry_row,
        "final_produced": len(rubrics),
        "complete": len(rubrics) == len(all_slides),
        "seconds": round(first["seconds"] + (retry_row["seconds"] if retry_row else 0), 1),
        "rubrics": rubrics,
    }
