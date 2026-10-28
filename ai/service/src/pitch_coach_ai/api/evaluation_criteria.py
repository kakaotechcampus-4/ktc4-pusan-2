"""평가 기준 분석 엔드포인트 (`docs/ai/features/evaluation-criteria.md` 5 · 8 · 11절).

- `POST /evaluation-criteria/parse`: 평가 기준 문장 → 표시 기준 · 요소별 값 · 숫자 목표 · 뺀 기준
  (`core.analyze_criteria`, LLM 1번)

- 부르는 곳: BE (평가 기준 입력 · 수정, 고친 표시 기준 저장 때, 요청 안 동기).
  경로는 `docs/ai/architecture.md` 1-2절
- LLM: 요청마다 마감 하나(AI 예산 25초, 기능 문서 8절)를 만들어 출력 스키마
  `EvaluationCriteriaAnalysis` 로 LLM 객체를 만든다. 역할 `evaluation_criteria`(출력 상한 2,048,
  `settings.LLM_ROLES`)
- 실패: 기본 결과가 없다(LLM 이 결과의 본체). 모델 설정이 없으면 503 `LLM_UNAVAILABLE`,
  LLM 실패는 504 `LLM_TIMEOUT` · 502 `LLM_ERROR` · 502 `LLM_INVALID_OUTPUT` 이다(코어는 잡지 않고
  올리고 `common/errors.py` 가 상태 코드로 바꾼다). 입력이 비었거나 2,000자를 넘으면 LLM 을 부르지
  않고 422 `VALIDATION_ERROR` 다
- `meta.model`: 코어는 null 로 두고, 라우터가 실제로 응답한 모델 이름을 싣는다
  (`architecture.md` 5절)
- 로그: `evaluation_criteria chars=… targets=… code=… duration_ms=…`.
  기준 문장 원문은 남기지 않는다 (기능 문서 11절, `architecture.md` 11절)
- 라우터는 `def` 다(코어가 동기)
"""

import logging
import time
from collections.abc import Callable
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from pitch_coach_ai.common.errors import ERROR_RESPONSES, LlmError
from pitch_coach_ai.common.llm import deadline_after, structured_llm
from pitch_coach_ai.features.evaluation_criteria import analyze_criteria
from pitch_coach_ai.features.evaluation_criteria.schemas import (
    EvaluationCriteriaAnalysis,
    EvaluationCriteriaRequest,
    EvaluationCriteriaResponse,
)
from pitch_coach_ai.settings import Settings

logger = logging.getLogger("pitch_coach_ai.evaluation_criteria")

router = APIRouter(
    prefix="/evaluation-criteria",
    tags=["evaluation_criteria"],
    responses=ERROR_RESPONSES,
)

#: 평가 기준 분석 요청 하나의 AI 예산(초) (기능 문서 8절, BE 시간 제한 30초)
BUDGET_SECONDS = 25
ROLE = "evaluation_criteria"

#: (스키마, 역할, 마감, 설정) → `.invoke` 를 가진 LLM 객체. 테스트가 가짜 LLM 으로 바꾼다
LlmFactory = Callable[[type[BaseModel], str, float, Settings], Any]


def _structured(schema: type[BaseModel], role: str, deadline: float, s: Settings) -> Any:
    return structured_llm(schema, role, deadline, app_settings=s)


def get_llm_factory() -> LlmFactory:
    return _structured


def _settings(request: Request) -> Settings:
    return request.app.state.settings


def _log(
    *, chars: int, started: float, targets: int | None = None, code: str | None = None
) -> None:
    """요청마다 한 줄. 성공이면 숫자 목표 수, 실패면 에러 코드(`code`)를 남긴다."""
    logger.info(
        "evaluation_criteria chars=%d targets=%s code=%s duration_ms=%.1f",
        chars,
        "-" if targets is None else targets,
        code or "-",
        (time.monotonic() - started) * 1000,
    )


@router.post("/parse")
def parse(
    req: EvaluationCriteriaRequest,
    s: Annotated[Settings, Depends(_settings)],
    factory: Annotated[LlmFactory, Depends(get_llm_factory)],
) -> EvaluationCriteriaResponse:
    started = time.monotonic()
    chars = len(req.standard_text)
    try:
        llm = factory(EvaluationCriteriaAnalysis, ROLE, deadline_after(BUDGET_SECONDS), s)
        res = analyze_criteria(req, llm)
    except LlmError as exc:
        _log(chars=chars, started=started, code=exc.code)
        raise
    res.meta.model = getattr(llm, "responded_model", None)
    _log(chars=chars, started=started, targets=len(res.targets))
    return res
