"""코치 엔드포인트 (`docs/ai/features/coach-agent.md` 5 · 11절). 지금은 `POST /coach/plan` 하나다.

- `POST /coach/plan`: 다음 Take 의 코칭 계획 (`core.plan_coaching`, LLM 1번)
- `/coach/evaluate` · `/coach/finalize` 는 아직 없다. 판정 모듈 넷(시선 · 말 속도 ·
  소리 크기 · 군더더기)이 서비스에 모두 있을 때 `Judges` 묶음과 함께 연다
  (코치 기능 문서 11절, `ai-service.md` 6-1 5번)

- 부르는 곳: BE (리뷰 저장 직후 · 대본 파싱 직후 등, 백그라운드).
  경로는 `docs/ai/architecture.md` 1-2절
- LLM: 요청마다 마감 하나(AI 예산 25초, 기능 문서 11절)를 만들어 출력 스키마 `PlanDraft` 로
  LLM 객체를 만든다. 역할 `coach.plan`(출력 상한 2,048, `settings.LLM_ROLES`).
  모델 설정이 없으면 `llm=None` 으로 코어를 불러 기본 계획 + `fallback_reason: LLM_UNAVAILABLE`
  을 200 으로 돌려준다(`ai-service.md` 6-2)
- 실패: LLM 호출 실패 · 시간 초과 · 잘못된 출력은 코어가 잡아 기본 계획 + `fallback_reason`
  200 이다. 요청 형식 오류(장 · 미션 개수, 미션 설명 500자 등)만 422 `VALIDATION_ERROR` 다
- `meta.model`: 코어는 LLM 계획을 썼을 때만 설정한 모델 이름을 싣는다. 실제로 응답한 모델
  이름이 있으면 그것으로 바꾼다(기본 계획이면 null 그대로, `architecture.md` 5절)
- 로그: `coach_plan slides=… missions=… fallback=… duration_ms=…`.
  대본 · 미션 설명 원문은 남기지 않는다
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
from pitch_coach_ai.features.coach import plan_coaching
from pitch_coach_ai.features.coach.schemas import PlanDraft, PlanRequest, PlanResponse
from pitch_coach_ai.settings import LLM_ROLES, Settings

logger = logging.getLogger("pitch_coach_ai.coach")

router = APIRouter(
    prefix="/coach",
    tags=["coach"],
    responses={code: ERROR_RESPONSES[code] for code in (422, 500)},
)

#: 코칭 계획 요청 하나의 AI 예산(초) (기능 문서 11절, `architecture.md` 3절)
PLAN_BUDGET_SECONDS = 25
PLAN_ROLE = "coach.plan"

#: (스키마, 역할, 마감, 설정) → `.invoke` 를 가진 LLM 객체. 테스트가 가짜 LLM 으로 바꾼다
LlmFactory = Callable[[type[BaseModel], str, float, Settings], Any]


def _structured(schema: type[BaseModel], role: str, deadline: float, s: Settings) -> Any:
    return structured_llm(schema, role, deadline, app_settings=s)


def get_llm_factory() -> LlmFactory:
    return _structured


def _settings(request: Request) -> Settings:
    return request.app.state.settings


def _model_name(s: Settings) -> str:
    """코어에 넘기는 모델 이름(설정이 없으면 빈 값). `planner_hash` 에 들어간다."""
    bundle = s.bundle(LLM_ROLES[PLAN_ROLE].bundle)
    return bundle.model if bundle is not None else ""


@router.post("/plan")
def plan(
    req: PlanRequest,
    s: Annotated[Settings, Depends(_settings)],
    factory: Annotated[LlmFactory, Depends(get_llm_factory)],
) -> PlanResponse:
    started = time.monotonic()
    try:
        llm = factory(PlanDraft, PLAN_ROLE, deadline_after(PLAN_BUDGET_SECONDS), s)
    except LlmUnavailable:
        llm = None
    res = plan_coaching(req, llm=llm, model=_model_name(s))
    if res.meta.model is not None:
        res.meta.model = getattr(llm, "responded_model", None) or res.meta.model
    logger.info(
        "coach_plan slides=%d missions=%d fallback=%s duration_ms=%.1f",
        len(req.scripts),
        len(req.missions),
        res.fallback_reason or "-",
        (time.monotonic() - started) * 1000,
    )
    return res
