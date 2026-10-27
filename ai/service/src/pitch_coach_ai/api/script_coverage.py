"""대본 전달도 엔드포인트 세 개 (`docs/ai/features/script-coverage-evaluation.md` 5 · 11절).

- `POST /scripts/rubric`: 대본 → 장별 평가 기준 (`core.analyze_script`)
- `POST /takes/analyze`: 장별 STT + 평가 기준 → 장별 채점 · Take 값 (`core.evaluate_take`)
- `POST /takes/rescore`: 저장한 채점 + 확인 답 → 다시 계산 (`core.rescore_evaluation`, LLM 없음)

- 부르는 곳: BE (백그라운드 · rescore 는 동기). 경로는 `docs/ai/architecture.md` 1-2절 그대로다.
  `/scripts` · `/takes` 아래에는 다른 기능 경로도 오므로(`/scripts/parse` · `/takes/review`)
  접두어를 두지 않고 경로를 다 적는다
- 형태소 분석기(Kiwi)를 쓰므로 시작할 때 불러오고(`WARMUP`), 준비 전에는 503 `NOT_READY` 다
- LLM: 요청마다 마감 하나(AI 예산 120초, 기능 문서 8절)를 만들어 출력 스키마 다섯 개로 LLM 객체를
  만든다. 역할 → 묶음 · 출력 상한 · 호출 한 번 시간 제한(`SCRIPT_COVERAGE_LLM_TIMEOUT_SECONDS`)은
  `settings.LLM_ROLES`. 설정이 없으면 코어에 `llms=None` 을 넘겨 503 `LLM_UNAVAILABLE` 이다.
  캐시는 쓰지 않는다(평가 기준은 BE 가 저장한다)
- 장별 실패: 코어가 장마다 LLM 오류를 잡아 `failed` 에 담고 200 을 돌려준다.
  대상 장이 모두 실패하거나 입력 오류면 코어의 `CoverageError` 를 공통 에러 본문으로 바꾼다
- `meta.model`: 코어는 설정한 모델 이름을 싣는다(LLM 출력을 쓴 결과가 있을 때만). 실제로 응답한
  모델 이름이 있으면 그것으로 바꾼다 (`architecture.md` 5절)
- 로그: `script_coverage op=… slides=… targets=… chars=… failed=… duration_ms=…`.
  대본 · STT 원문은 남기지 않는다 (`architecture.md` 11절)
- 라우터는 `def` 다. 코어가 동기이고 안에서 스레드 풀을 쓰므로 이벤트 루프를 막지 않게 한다
"""

import logging
import time
from collections.abc import Callable, Sequence
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from pitch_coach_ai.common.errors import ERROR_RESPONSES, AppError, LlmUnavailable
from pitch_coach_ai.common.llm import deadline_after, structured_llm
from pitch_coach_ai.common.readiness import require_ready
from pitch_coach_ai.features.script_coverage.core import (
    CoverageError,
    ScriptLLMs,
    TakeLLMs,
    analyze_script,
    evaluate_take,
    rescore_evaluation,
)
from pitch_coach_ai.features.script_coverage.schemas import (
    AnalyzeRequest,
    EvaluationResponse,
    RescoreRequest,
    RubricRequest,
    RubricResponse,
)
from pitch_coach_ai.features.script_coverage.script_analysis.schemas import (
    FinalReview,
    SlideSemanticAnalysis,
    SlideUnits,
)
from pitch_coach_ai.features.script_coverage.shared.kiwi import get_kiwi
from pitch_coach_ai.features.script_coverage.stt_evaluation.schemas import (
    SemanticEvaluation,
    VerifierResult,
)
from pitch_coach_ai.settings import LLM_ROLES, Settings

logger = logging.getLogger("pitch_coach_ai.script_coverage")

router = APIRouter(
    tags=["script_coverage"],
    dependencies=[Depends(require_ready)],
    responses=ERROR_RESPONSES,
)

#: 시작할 때 불러올 것 (`common/readiness.py`). Kiwi 는 처음 만들 때 모델을 읽어 느리다
WARMUP = [get_kiwi]

#: 평가 기준 만들기 · 채점 요청 하나의 AI 예산(초) (기능 문서 8절, BE 시간 제한 130초)
BUDGET_SECONDS = 120

#: 코어 LLM 묶음의 칸 → (역할, 출력 스키마)
SCRIPT_ROLES: dict[str, tuple[str, type[BaseModel]]] = {
    "semantic": ("script_coverage.semantic", SlideSemanticAnalysis),
    "final": ("script_coverage.final", FinalReview),
    "unit": ("script_coverage.unit", SlideUnits),
}
TAKE_ROLES: dict[str, tuple[str, type[BaseModel]]] = {
    "eval": ("script_coverage.eval", SemanticEvaluation),
    "verifier": ("script_coverage.verifier", VerifierResult),
}

#: (스키마, 역할, 마감, 설정) → `.invoke` 를 가진 LLM 객체. 테스트가 가짜 LLM 으로 바꾼다
LlmFactory = Callable[[type[BaseModel], str, float, Settings], Any]


def _structured(schema: type[BaseModel], role: str, deadline: float, s: Settings) -> Any:
    return structured_llm(schema, role, deadline, app_settings=s)


def get_llm_factory() -> LlmFactory:
    return _structured


def _settings(request: Request) -> Settings:
    return request.app.state.settings


def _build_llms(
    roles: dict[str, tuple[str, type[BaseModel]]], factory: LlmFactory, s: Settings
) -> dict[str, Any] | None:
    """요청 하나의 LLM 객체들. 모델 설정이 없으면 None (코어가 503 `LLM_UNAVAILABLE` 로 낸다)."""
    deadline = deadline_after(BUDGET_SECONDS)
    try:
        return {slot: factory(schema, role, deadline, s) for slot, (role, schema) in roles.items()}
    except LlmUnavailable:
        return None


def _model_name(roles: dict[str, tuple[str, type[BaseModel]]], s: Settings) -> str:
    """코어에 넘기는 모델 이름(설정이 없으면 빈 값).

    평가 기준 `meta.llm_model` 과 설정 해시에 들어간다.
    """
    role = LLM_ROLES[next(iter(roles.values()))[0]]
    bundle = s.bundle(role.bundle)
    return bundle.model if bundle is not None else ""


def _responded(llms: dict[str, Any] | None, configured: str | None) -> str | None:
    """`meta.model`: LLM 출력을 쓴 결과가 없으면(코어가 None) None, 있으면 응답한 모델 이름."""
    if configured is None or llms is None:
        return configured
    for llm in llms.values():
        name = getattr(llm, "responded_model", None)
        if name:
            return name
    return configured


def _app_error(exc: CoverageError) -> AppError:
    """코어의 요청 전체 실패 → 공통 에러 본문 (`{"error": {code, message}}`)."""
    error = AppError(exc.message)
    error.status_code = exc.status_code
    error.code = exc.code
    return error


def _chars(req: RubricRequest | AnalyzeRequest) -> int:
    """요청의 대본 · STT 글자 수 합 (원문 대신 로그에 남긴다)."""
    if isinstance(req, RubricRequest):
        return sum(len(x.script) for x in req.slides)
    return sum(len(x.stt) for x in req.slides)


def _targets(numbers: Sequence[int] | None, all_numbers: Sequence[int]) -> int:
    return len(set(numbers) if numbers is not None else set(all_numbers))


def _log(
    op: str,
    *,
    slides: int,
    targets: int,
    chars: int,
    started: float,
    failed: int | None = None,
    code: str | None = None,
) -> None:
    """요청마다 한 줄. 성공이면 실패한 장 수, 요청 전체 실패면 에러 코드(`code`)를 남긴다."""
    logger.info(
        "script_coverage op=%s slides=%d targets=%d chars=%d failed=%s code=%s duration_ms=%.1f",
        op,
        slides,
        targets,
        chars,
        "-" if failed is None else failed,
        code or "-",
        (time.monotonic() - started) * 1000,
    )


@router.post("/scripts/rubric")
def rubric(
    req: RubricRequest,
    s: Annotated[Settings, Depends(_settings)],
    factory: Annotated[LlmFactory, Depends(get_llm_factory)],
) -> RubricResponse:
    started = time.monotonic()
    numbers = [x.slide_number for x in req.slides]
    counts = {
        "slides": len(numbers),
        "targets": _targets(req.slide_numbers, numbers),
        "chars": _chars(req),
    }
    llms = _build_llms(SCRIPT_ROLES, factory, s)
    try:
        res = analyze_script(
            req,
            llms=ScriptLLMs(**llms) if llms is not None else None,
            model=_model_name(SCRIPT_ROLES, s),
        )
    except CoverageError as exc:
        _log("rubric", **counts, started=started, code=exc.code)
        raise _app_error(exc) from exc
    res.meta.model = _responded(llms, res.meta.model)
    _log("rubric", **counts, started=started, failed=len(res.failed))
    return res


@router.post("/takes/analyze")
def analyze(
    req: AnalyzeRequest,
    s: Annotated[Settings, Depends(_settings)],
    factory: Annotated[LlmFactory, Depends(get_llm_factory)],
) -> EvaluationResponse:
    started = time.monotonic()
    numbers = [x.slide_number for x in req.slides]
    counts = {
        "slides": len(numbers),
        "targets": _targets(req.slide_numbers, numbers),
        "chars": _chars(req),
    }
    llms = _build_llms(TAKE_ROLES, factory, s)
    try:
        res = evaluate_take(
            req,
            llms=TakeLLMs(**llms) if llms is not None else None,
            model=_model_name(TAKE_ROLES, s),
        )
    except CoverageError as exc:
        _log("analyze", **counts, started=started, code=exc.code)
        raise _app_error(exc) from exc
    res.meta.model = _responded(llms, res.meta.model)
    _log("analyze", **counts, started=started, failed=len(res.failed))
    return res


@router.post("/takes/rescore")
def rescore(req: RescoreRequest) -> EvaluationResponse:
    started = time.monotonic()
    numbers = {e.slide_number for e in req.evaluations} | {f.slide_number for f in req.failed}
    counts = {"slides": len(numbers), "targets": len(numbers), "chars": 0}
    try:
        res = rescore_evaluation(req)
    except CoverageError as exc:
        _log("rescore", **counts, started=started, code=exc.code)
        raise _app_error(exc) from exc
    _log("rescore", **counts, started=started, failed=len(res.failed))
    return res
