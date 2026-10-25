"""서비스가 부르는 진입 함수와 오류 모양.

세 진입 함수(대본 평가 기준 · Take 채점 · 확인 반영)가 하위 함수(`script_analysis` · `stt_evaluation`)를
감싸 API 모양(`failed` · `slide_numbers` · Take 값)으로 바꾼다. 하위 함수는 장별 실패를 `stats["failed"]`
({장 번호: 에러 코드})로 돌려주고, 여기서 `failed` 목록(고정 문구)으로 만든다. 지금은 `analyze_script`
(`/scripts/rubric`)만 있다.

- 요청 전체를 실패로 돌려줄 때는 `CoverageError` 를 낸다. API 가 `status_code` 와 `{"error": {code, message}}` 로 바꾼다.
- 일부 장만 실패하면 성공한 장은 `rubrics` 에, 실패한 장은 `failed` 에 담아 200 으로 돌려준다.
  대상 장이 모두 실패하면 요청 전체 실패다 (architecture.md 4절 '장별 API').
- LLM 과 캐시는 인자로 받는다. 코어는 파일 · 환경변수 · 시계를 쓰지 않는다.
"""

from dataclasses import dataclass
from typing import Any

from .config import MAX_WORKERS
from .schemas import ErrorBody, ResponseMeta, RubricRequest, RubricResponse, SlideFailure
from .script_analysis.core import analyze_script as analyze_slides
from .script_analysis.schemas import SlideScript
from .shared.llm_step import ERROR_MESSAGES, LLM_ERROR_CODES, LLMCache
from .version import FEATURE_VERSION, SCHEMA_VERSION

# 에러 코드 → HTTP 상태 (기능 문서 7절). LLM 코드의 문구는 `ERROR_MESSAGES`
STATUS_CODES = {
    "LLM_TIMEOUT": 504,
    "LLM_ERROR": 502,
    "LLM_INVALID_OUTPUT": 502,
    "LLM_UNAVAILABLE": 503,
    "RUBRIC_MISSING": 422,
    "RUBRIC_MISMATCH": 422,
    "STT_TEXT_MISSING": 422,
}
ALL_RUBRIC_MISSING_MESSAGE = "채점할 장의 평가 기준이 없습니다."


class CoverageError(Exception):
    """요청 전체를 실패로 돌려줄 때 (API 가 status_code 와 {"error": {code, message}} 로 바꾼다)."""

    def __init__(self, status_code: int, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.status_code = status_code
        self.code = code
        self.message = message


def llm_error(code: str) -> CoverageError:
    """LLM 에러 코드 → 요청 전체 오류 (상태 · 고정 문구)."""
    return CoverageError(STATUS_CODES[code], code, ERROR_MESSAGES[code])


@dataclass(frozen=True)
class ScriptLLMs:
    """`/scripts/rubric` 의 LLM 묶음. 출력 스키마마다 하나다 (기능 문서 6-5)."""

    semantic: Any  # SlideSemanticAnalysis
    final: Any  # FinalReview
    unit: Any  # SlideUnits


@dataclass(frozen=True)
class TakeLLMs:
    """`/takes/analyze` 의 LLM 묶음 (의미 평가 · 교차 검증)."""

    eval: Any  # SemanticEvaluation
    verifier: Any  # VerifierResult


def _all_failed(failed: dict[int, str]) -> CoverageError:
    """대상 장이 모두 실패했을 때의 요청 전체 오류.

    LLM 코드가 하나라도 있으면 그중 장 번호가 가장 작은 장의 코드 · 상태 · 고정 문구를 쓴다.
    모두 `RUBRIC_MISSING` 일 때만 422 `RUBRIC_MISSING` 이다.
    """
    for number in sorted(failed):
        if failed[number] in LLM_ERROR_CODES:
            return llm_error(failed[number])
    code = "RUBRIC_MISSING"
    return CoverageError(STATUS_CODES[code], code, ALL_RUBRIC_MISSING_MESSAGE)


def _failures(failed: dict[int, str]) -> list[SlideFailure]:
    return [
        SlideFailure(slide_number=n, error=ErrorBody(code=c, message=ERROR_MESSAGES[c]))
        for n, c in sorted(failed.items())
    ]


def analyze_script(
    request: RubricRequest | dict,
    *,
    llms: ScriptLLMs | None,
    model: str,
    cache: LLMCache | None = None,
) -> RubricResponse:
    """`/scripts/rubric`: 대본의 장별 평가 기준을 만든다.

    `slide_numbers` 가 있으면 그 장만 만들고, 나머지 장은 키워드 · 이름 판정의 맥락으로만 쓴다.
    `slide_numbers` 가 빈 목록이면 대상이 없으므로 빈 결과를 돌려준다. `llms` 가 None 이면
    (LLM 설정 없음) 요청 전체가 503 `LLM_UNAVAILABLE` 이다.
    """
    request = RubricRequest.model_validate(request)
    if llms is None:
        raise llm_error("LLM_UNAVAILABLE")
    slides = [SlideScript(slide_number=s.slide_number, script=s.script) for s in request.slides]
    rubrics, stats = analyze_slides(
        request.script_version_id,
        slides,
        semantic_llm=llms.semantic,
        final_llm=llms.final,
        unit_llm=llms.unit,
        model=model,
        cache=cache,
        max_workers=MAX_WORKERS,
        targets=request.slide_numbers,
    )
    if stats["slides"] and not rubrics:
        raise _all_failed(stats["failed"])
    return RubricResponse(
        rubrics=sorted(rubrics, key=lambda r: r.meta.slide_number),
        failed=_failures(stats["failed"]),
        meta=ResponseMeta(
            schema_version=SCHEMA_VERSION, feature_version=FEATURE_VERSION, model=model
        ),
    )
