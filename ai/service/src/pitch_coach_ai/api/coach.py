"""코치 엔드포인트 (`docs/ai/features/coach-agent.md` 5 · 11절).

- `POST /coach/evaluate`: 발표 중 1초 판단 (`core.decide_safe`). LLM 을 쓰지 않는다
- `POST /coach/finalize`: Take 종료 뒤 Take 결과 (`core.finalize`). 다 세지 못했으면 409
  `REPLAY_REQUIRED` 이고, BE 가 Take 전체 원자료(`replay`)를 실어 다시 부른다
- `POST /coach/plan`: 다음 Take 의 코칭 계획 (`core.plan_coaching`, LLM 1번)

- 부르는 곳: BE. evaluate 는 진행 중인 Take 마다 1초에 한 번(BE 시간 제한 약 300 ms),
  finalize · plan 은 백그라운드. 경로는 `docs/ai/architecture.md` 1-2절
- 판정 모듈 묶음(`Judges`): 코치는 판정 모듈을 import 하지 않는다. 이 파일이 서비스의 판정 모듈
  넷(시선 · 말 속도 · 소리 크기 · 군더더기)과 소리 크기의 기준 음량 함수 둘(`speaking_levels` ·
  `baseline`)을 묶어 앱을 불러올 때 한 번 만든다(`JUDGES`). 시간 판정은 코치 안에 있다
- 실패
  - evaluate: 코치 안의 예외는 코어가 200 `WAIT` + `INTERNAL_ERROR` 로 바꾼다. 요청 형식 오류
    (필수 필드 · `t_ms` 상한 · 배열 한도)만 422 `VALIDATION_ERROR` 다
  - finalize: `ReplayRequired` → 409 `REPLAY_REQUIRED`(메시지에 빠진 구간), 형식 오류는 422
  - plan: LLM 실패 · 시간 초과 · 잘못된 출력 · 설정 없음은 코어가 기본 계획 + `fallback_reason`
    200 으로 돌려준다. 요청 형식 오류(장 · 미션 개수, 미션 설명 500자 등)만 422 다
- plan 의 LLM: 요청마다 마감 하나(AI 예산 25초, 기능 문서 8절)를 만들어 출력 스키마 `PlanDraft`
  로 LLM 객체를 만든다. 역할 `coach.plan`(출력 상한 2,048, `settings.LLM_ROLES`).
  `meta.model` 은 코어가 LLM 계획을 썼을 때만 설정한 모델 이름을 싣고, 실제로 응답한 모델 이름이
  있으면 그것으로 바꾼다(기본 계획이면 null 그대로, `architecture.md` 5절)
- 로그 (요청 본문 · 단어 · 대본 원문은 남기지 않는다. 걸린 시간은 요청마다 요청 로그에도 있다)
  - evaluate: 새로 시작(`STATE_RESET`) · 크게 앞선 시각(`T_MS_JUMP`) · 코치 안 예외
    (`INTERNAL_ERROR`)가 난 응답만 `coach_evaluate take_id=… t_ms=… reasons=… duration_ms=…`.
    1초마다 오는 정상 응답은 남기지 않는다. 리셋 건수는 이 줄을 센다(기능 문서 11절 증상표)
  - finalize: 요청마다 `coach_finalize take_id=… t_ms=… replay=… result=… duration_ms=…`
  - plan: `coach_plan slides=… missions=… fallback=… duration_ms=…`
- 라우터는 `def` 다(코어가 동기 — 스레드 풀에서 돈다. `async def` 안에서 부르면 서버가 멈춘다)
"""

import logging
import time
from collections.abc import Callable
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request, status
from pydantic import BaseModel

from pitch_coach_ai.common.errors import (
    ERROR_RESPONSES,
    AppError,
    ErrorResponse,
    LlmUnavailable,
    log_safe,
)
from pitch_coach_ai.common.llm import deadline_after, structured_llm
from pitch_coach_ai.features.coach import (
    Judges,
    ReplayRequired,
    decide_safe,
    finalize,
    plan_coaching,
)
from pitch_coach_ai.features.coach.schemas import (
    CoachRequest,
    CoachResponse,
    FinalizeRequest,
    FinalizeResponse,
    PlanDraft,
    PlanRequest,
    PlanResponse,
)
from pitch_coach_ai.features.filler import core as filler
from pitch_coach_ai.features.gaze import core as gaze
from pitch_coach_ai.features.pace import core as pace
from pitch_coach_ai.features.volume import core as volume
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

#: evaluate 에서 로그를 남기는 이유 코드 (정상 1초 응답은 남기지 않는다)
_NOTED_REASONS = ("STATE_RESET", "T_MS_JUMP", "INTERNAL_ERROR")

#: (스키마, 역할, 마감, 설정) → `.invoke` 를 가진 LLM 객체. 테스트가 가짜 LLM 으로 바꾼다
LlmFactory = Callable[[type[BaseModel], str, float, Settings], Any]


def build_judges() -> Judges:
    """서비스의 판정 모듈 넷을 코치가 받는 묶음으로 만든다. 기준 음량은 소리 크기 모듈로 잡는다."""
    return Judges(
        gaze=gaze,
        pace=pace,
        volume=volume,
        filler=filler,
        speaking_levels=volume.speaking_levels,
        baseline=volume.baseline,
    )


#: 앱을 불러올 때 한 번 만든다. 판정 모듈은 상태가 없어 요청 · 스레드 사이에 함께 쓴다
JUDGES = build_judges()


def get_judges() -> Judges:
    """테스트가 다른 묶음으로 바꿔 끼운다."""
    return JUDGES


class ReplayRequiredError(AppError):
    """센 구간이 Take 를 덮지 못했다. BE 가 `replay` 를 실어 다시 부른다(기능 문서 7절)."""

    status_code = status.HTTP_409_CONFLICT
    code = "REPLAY_REQUIRED"
    message = "센 구간이 Take 를 덮지 못했다. Take 전체 원자료(replay)를 실어 다시 불러야 한다."


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


def _ms_since(started: float) -> float:
    return (time.monotonic() - started) * 1000


@router.post("/evaluate")
def evaluate(
    req: CoachRequest,
    judges: Annotated[Judges, Depends(get_judges)],
) -> CoachResponse:
    started = time.monotonic()
    res = decide_safe(req, judges)
    noted = [code for code in res.reason_codes if code in _NOTED_REASONS]
    if noted:
        level = logging.WARNING if "INTERNAL_ERROR" in noted else logging.INFO
        logger.log(
            level,
            "coach_evaluate take_id=%s t_ms=%d reasons=%s duration_ms=%.1f",
            log_safe(req.take_id),
            req.t_ms,
            ",".join(noted),
            _ms_since(started),
        )
    return res


@router.post(
    "/finalize",
    responses={
        status.HTTP_409_CONFLICT: {
            "model": ErrorResponse,
            "description": "센 구간이 Take 를 덮지 못함(REPLAY_REQUIRED). replay 를 실어 다시",
        }
    },
)
def finalize_take(
    req: FinalizeRequest,
    judges: Annotated[Judges, Depends(get_judges)],
) -> FinalizeResponse:
    started = time.monotonic()
    replay = req.replay is not None
    try:
        res = finalize(req, judges)
    except ReplayRequired as exc:
        missing_ms = sum(hi - lo for lo, hi in exc.missing)
        logger.info(
            "coach_finalize take_id=%s t_ms=%d replay=%s result=REPLAY_REQUIRED missing_ms=%d "
            "duration_ms=%.1f",
            log_safe(req.take_id),
            req.t_ms,
            replay,
            missing_ms,
            _ms_since(started),
        )
        # 메시지는 코어가 만든 빠진 구간 문장이다(숫자뿐, 사용자 입력이 없다)
        raise ReplayRequiredError(str(exc)) from None
    logger.info(
        "coach_finalize take_id=%s t_ms=%d replay=%s result=OK events=%d duration_ms=%.1f",
        log_safe(req.take_id),
        req.t_ms,
        replay,
        len(req.events),
        _ms_since(started),
    )
    return res


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
        _ms_since(started),
    )
    return res
