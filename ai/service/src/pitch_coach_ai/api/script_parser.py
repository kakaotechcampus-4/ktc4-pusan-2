"""대본 파싱 엔드포인트 `POST /scripts/parse` (`docs/ai/features/script-parser.md` 5 · 7 · 8 · 11절)

- 부르는 곳: BE (대본 등록 · 수정 · 다시 파싱, 백그라운드). 경로는 `docs/ai/architecture.md` 1-2절.
  `/scripts` 아래에는 대본 전달도 경로(`/scripts/rubric`)도 있어 접두어를 두지 않고 경로를 다 적는다
- LLM: 요청마다 마감 하나(AI 예산 25초, 기능 문서 8절)를 만들어 출력 스키마 `LLMOutput` 으로
  LLM 객체를 만든다. 역할 `script_parser`(출력 상한 2,000 · 추론 `low`, `settings.LLM_ROLES`).
  호출 한 번 시간 제한은 `LLM_TIMEOUT_SECONDS`(기본 12초), 재시도는 마감 안에서만 1번
- 입력 검사가 먼저다: 모델 설정이 없어도 빈 대본은 422 `EMPTY_SCRIPT` 다(기능 문서 6-1 1번).
  그래서 LLM 을 만들지 못하면 부를 때 `LLM_UNAVAILABLE` 을 내는 대역을 코어에 넘긴다
- 실패: 코어의 `EmptyScriptError` → 422 `EMPTY_SCRIPT`. LLM 오류(시간 초과 · 호출 실패 · 잘못된
  출력 · 설정 없음)는 코어가 잡지 않고 올리고, `common/errors.py` 가 504 · 502 · 503 으로 바꾼다
- `meta.model`: 코어는 비워 두고 여기서 실제로 응답한 모델 이름을 넣는다(`architecture.md` 5절)
- 로그: `script_parser chars=… slides=… status=… code=… duration_ms=…`.
  대본 원문 · 키워드 · 용어는 남기지 않는다(기능 문서 11절 '로그')
- 라우터는 `def` 다(코어가 동기)
"""

import logging
import time
from collections.abc import Callable
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request, status
from pydantic import BaseModel

from pitch_coach_ai.common.errors import ERROR_RESPONSES, AppError, LlmUnavailable
from pitch_coach_ai.common.llm import deadline_after, structured_llm
from pitch_coach_ai.features.script_parser import EmptyScriptError, parse_script
from pitch_coach_ai.features.script_parser.schemas import LLMOutput, ParseRequest, ParseResponse
from pitch_coach_ai.settings import LLM_ROLES, Settings

logger = logging.getLogger("pitch_coach_ai.script_parser")

router = APIRouter(tags=["script_parser"], responses=ERROR_RESPONSES)

#: 대본 파싱 요청 하나의 AI 예산(초) (기능 문서 8절, BE 시간 제한 30초)
BUDGET_SECONDS = 25
ROLE = "script_parser"

#: (스키마, 역할, 마감, 설정) → `.invoke` 를 가진 LLM 객체. 테스트가 가짜 LLM 으로 바꾼다
LlmFactory = Callable[[type[BaseModel], str, float, Settings], Any]


class EmptyScript(AppError):
    """구분자 · 공백 · 제목 줄을 빼면 대본에 글이 없다. BE 는 다시 부르지 않는다."""

    status_code = status.HTTP_422_UNPROCESSABLE_CONTENT
    code = "EMPTY_SCRIPT"
    message = "대본에 글이 없습니다."


class _Unavailable:
    """모델 설정이 없을 때 코어에 넘기는 대역. 코어가 LLM 을 부를 때만 `LLM_UNAVAILABLE` 이다."""

    responded_model = None

    def __init__(self, error: LlmUnavailable) -> None:
        self._error = error

    def invoke(self, messages: Any) -> Any:
        raise self._error


def _structured(schema: type[BaseModel], role: str, deadline: float, s: Settings) -> Any:
    return structured_llm(schema, role, deadline, app_settings=s)


def get_llm_factory() -> LlmFactory:
    return _structured


def _settings(request: Request) -> Settings:
    return request.app.state.settings


def _configured_model(s: Settings) -> str | None:
    bundle = s.bundle(LLM_ROLES[ROLE].bundle)
    return bundle.model if bundle is not None else None


@router.post("/scripts/parse")
def parse(
    req: ParseRequest,
    s: Annotated[Settings, Depends(_settings)],
    factory: Annotated[LlmFactory, Depends(get_llm_factory)],
) -> ParseResponse:
    started = time.monotonic()
    try:
        llm = factory(LLMOutput, ROLE, deadline_after(BUDGET_SECONDS), s)
    except LlmUnavailable as exc:
        llm = _Unavailable(exc)
    try:
        res = parse_script(req, llm)
    except EmptyScriptError as exc:
        _log(req, started, code=EmptyScript.code)
        raise EmptyScript(str(exc)) from exc
    except AppError as exc:
        _log(req, started, code=exc.code)
        raise
    res.meta.model = getattr(llm, "responded_model", None) or _configured_model(s)
    _log(req, started, slides=len(res.slides), outcome=res.status)
    return res


def _log(
    req: ParseRequest,
    started: float,
    *,
    slides: int | None = None,
    outcome: str | None = None,
    code: str | None = None,
) -> None:
    """요청마다 한 줄. 대본 원문 · 키워드 · 용어는 남기지 않는다."""
    logger.info(
        "script_parser chars=%d target_ms=%s slides=%s status=%s code=%s duration_ms=%.1f",
        len(req.script_text),
        "-" if req.target_ms is None else req.target_ms,
        "-" if slides is None else slides,
        outcome or "-",
        code or "-",
        (time.monotonic() - started) * 1000,
    )
