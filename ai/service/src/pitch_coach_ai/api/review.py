"""리뷰 엔드포인트 (`docs/ai/features/review-agent.md` 5 · 8 · 11절).

- `POST /takes/review`: Take 결과 · 대본 전달도 · 이전 Take · 미션 · 사용자 기준 → 리뷰
  (`core.review_take`, 규칙 단계 뒤 LLM 1번)

- 부르는 곳: BE (대본 전달도 채점 · 확인이 끝난 뒤, 백그라운드).
  경로는 `docs/ai/architecture.md` 1-2절
- LLM: 요청마다 마감 하나(AI 예산 25초, 기능 문서 8절)를 만들어 출력 스키마 `ReviewDraft` 로
  LLM 객체를 만든다. 역할 `review`(`settings.LLM_ROLES`). 모델 설정이 없으면 `llm=None` 으로
  코어를 불러 기본 결과 + `fallback_reason: LLM_UNAVAILABLE` 을 200 으로 돌려준다
  (`ai-service.md` 6-2)
- 실패: LLM 호출 실패 · 시간 초과 · 잘못된 출력은 코어가 잡아 기본 결과(기본 문장 · 후보 상위 다음
  미션) + `fallback_reason` 200 이다. 숫자 · 상태 · 점수 · 미션 판정은 LLM 과 상관없이 같다.
  요청 형식 오류(개수 · 길이 한도 등)만 422 `VALIDATION_ERROR` 다
- `meta.model`: 코어는 LLM 결과일 때만 설정한 모델 이름을 싣는다. 실제로 응답한 모델 이름이
  있으면 그것으로 바꾼다(기본 결과면 null 그대로, `architecture.md` 5절)
- 로그: 코어가 차원 상태 · 점수 · `fallback_reason` · 숫자 검사로 바꾼 문장 수를 남기고, 라우터는
  `review previous_takes=… missions=… fallback=… duration_ms=…` 한 줄을 더한다.
  리뷰 · 대본 · 미션 문장 원문은 남기지 않는다 (기능 문서 11절, `architecture.md` 11절)
- 라우터는 `def` 다(코어가 동기)
"""

import logging
import time
from collections.abc import Callable
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from pitch_coach_ai.common.errors import ERROR_RESPONSES, LlmUnavailable
from pitch_coach_ai.common.llm import deadline_after, structured_llm
from pitch_coach_ai.features.review import review_take
from pitch_coach_ai.features.review.schemas import ReviewDraft, ReviewRequest, ReviewResponse
from pitch_coach_ai.settings import LLM_ROLES, Settings

logger = logging.getLogger("pitch_coach_ai.review")

router = APIRouter(
    tags=["review"],
    responses={code: ERROR_RESPONSES[code] for code in (422, 500)},
)

#: 리뷰 요청 하나의 AI 예산(초) (기능 문서 8절, BE 시간 제한 30초)
BUDGET_SECONDS = 25
ROLE = "review"

#: (스키마, 역할, 마감, 설정) → `.invoke` 를 가진 LLM 객체. 테스트가 가짜 LLM 으로 바꾼다
LlmFactory = Callable[[type[BaseModel], str, float, Settings], Any]


def _structured(schema: type[BaseModel], role: str, deadline: float, s: Settings) -> Any:
    return structured_llm(schema, role, deadline, app_settings=s)


def get_llm_factory() -> LlmFactory:
    return _structured


def _settings(request: Request) -> Settings:
    return request.app.state.settings


def _model_name(s: Settings) -> str | None:
    """코어에 넘기는 모델 이름(설정이 없으면 None). LLM 결과일 때만 `meta.model` 에 실린다."""
    bundle = s.bundle(LLM_ROLES[ROLE].bundle)
    return bundle.model if bundle is not None else None


@router.post("/takes/review")
def review(
    req: ReviewRequest,
    s: Annotated[Settings, Depends(_settings)],
    factory: Annotated[LlmFactory, Depends(get_llm_factory)],
) -> ReviewResponse:
    started = time.monotonic()
    try:
        llm = factory(ReviewDraft, ROLE, deadline_after(BUDGET_SECONDS), s)
    except LlmUnavailable:
        llm = None
    res = review_take(req, llm, model=_model_name(s))
    if res.meta.model is not None:
        res.meta.model = getattr(llm, "responded_model", None) or res.meta.model
    logger.info(
        "review previous_takes=%d missions=%d fallback=%s duration_ms=%.1f",
        len(req.previous_takes or []),
        len(req.missions or []),
        res.fallback_reason or "-",
        (time.monotonic() - started) * 1000,
    )
    return res
