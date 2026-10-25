"""예외 → 공통 에러 본문 · 상태 코드.

모든 에러 응답은 한 모양이다 (BE 와의 계약, `docs/ai/architecture.md` 4절).

    {"error": {"code": "LLM_TIMEOUT", "message": "LLM 응답 시간이 초과되었습니다."}}

- 부르는 곳: `main.py` 의 `register_exception_handlers(app)`. `common/llm.py` 는 아래
  LLM 예외를 던지고, 기능은 `AppError` 를 상속해 자기 422 예외를 만든다.

      class InsufficientTranscript(AppError):
          status_code = 422
          code = "INSUFFICIENT_TRANSCRIPT"
          message = "분석할 발화가 너무 적습니다."

- `message` 는 사람이 읽는 한국어 설명이다. 예외 내용 · stack trace 는 응답에 넣지 않고
  로그에만 남긴다. 요청 본문(대본 · STT 등 사용자 입력)은 로그에도 남기지 않는다
- 처리하는 순서: 우리 예외(`AppError`) → 요청 형식 오류(422) → 라우터의 404 · 405 →
  그 밖의 예외(500). 500 은 `request_log` 미들웨어가 먼저 잡아 같은 본문을 만든다
  (그래야 응답에 `X-Request-ID` 가 붙는다. 이 파일의 500 처리기는 미들웨어 밖의 예비)
- BE(`core/exceptions.py`)와 다른 점: 본문을 `error` 로 한 겹 감싸고 `detail` 이 없다.
  500 의 코드는 `INTERNAL_ERROR` 다
"""

import logging
import re
import traceback
from http import HTTPStatus
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.exceptions import HTTPException

logger = logging.getLogger("pitch_coach_ai.errors")


class AppError(Exception):
    """서비스의 기본 예외. 상속해서 `status_code` · `code` · `message` 를 정한다."""

    status_code: int = status.HTTP_400_BAD_REQUEST
    code: str = "BAD_REQUEST"
    message: str = "잘못된 요청입니다."

    def __init__(self, message: str | None = None) -> None:
        if message is not None:
            self.message = message
        super().__init__(self.message)


class LlmError(AppError):
    """LLM 호출 실패.

    `code` 는 기본 결과를 내는 기능(코칭 계획 · 리뷰)의 `fallback_reason` 으로도 쓴다.
    """


class LlmTimeout(LlmError):
    status_code = status.HTTP_504_GATEWAY_TIMEOUT
    code = "LLM_TIMEOUT"
    message = "LLM 응답 시간이 초과되었습니다."


class LlmCallFailed(LlmError):
    status_code = status.HTTP_502_BAD_GATEWAY
    code = "LLM_ERROR"
    message = "LLM 호출에 실패했습니다."


class LlmInvalidOutput(LlmError):
    status_code = status.HTTP_502_BAD_GATEWAY
    code = "LLM_INVALID_OUTPUT"
    message = "LLM 출력이 정해진 형식과 다릅니다."


class LlmUnavailable(LlmError):
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    code = "LLM_UNAVAILABLE"
    message = "지금은 LLM 을 쓸 수 없습니다."


class NotReady(AppError):
    """시작할 때 불러오는 모델(예: 형태소 분석기)이 아직 준비되지 않았다. BE 는 다시 부른다."""

    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    code = "NOT_READY"
    message = "서버가 아직 준비 중입니다. 잠시 뒤 다시 요청해 주세요."


class ErrorBody(BaseModel):
    code: str
    message: str


class ErrorResponse(BaseModel):
    """모든 에러 응답의 모양. OpenAPI 문서(`contracts/openapi.json`)에 이 모양을 싣는 데 쓴다."""

    error: ErrorBody


def _error_doc(description: str) -> dict[str, Any]:
    return {"model": ErrorResponse, "description": description}


#: 라우터의 `responses=` 에 넣는 에러 응답 문서.
#: FastAPI 가 기본으로 싣는 422(`detail`)를 공통 본문으로 덮는다
ERROR_RESPONSES: dict[int, dict[str, Any]] = {
    422: _error_doc("요청 형식 오류(VALIDATION_ERROR) 또는 기능의 입력 오류"),
    500: _error_doc("처리되지 않은 예외(INTERNAL_ERROR)"),
    502: _error_doc("LLM 호출 실패(LLM_ERROR) · 잘못된 출력(LLM_INVALID_OUTPUT)"),
    503: _error_doc("LLM 을 쓸 수 없음(LLM_UNAVAILABLE) · 준비 전(NOT_READY)"),
    504: _error_doc("LLM 응답 시간 초과(LLM_TIMEOUT)"),
}


_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def log_safe(value: object, limit: int = 200) -> str:
    """로그 한 줄에 넣을 값. 공백 · 제어 문자를 이스케이프하고 길이를 자른다.

    요청 경로 · 요청 ID 는 밖에서 온 값이라, 그대로 두면 `key=value` 로그에 가짜 필드를
    끼워 넣거나(`X-Request-ID: x status=200`) 터미널 제어 문자를 보낼 수 있다.
    """
    text = str(value)
    cleaned = "".join(
        ch if ch.isprintable() and not ch.isspace() else f"\\x{ord(ch):02x}" for ch in text
    )
    return cleaned if len(cleaned) <= limit else cleaned[:limit] + "..."


def exception_summary(exc: BaseException) -> str:
    """예외 종류와 위치(파일:줄:함수)만. 예외 메시지 · 지역 변수는 넣지 않는다.

    예외 메시지에는 대본 · STT 같은 사용자 원문이 섞일 수 있다(`architecture.md` 11절).
    예: `RuntimeError at core.py:42:judge <- sample.py:30:judge_route`
    """
    frames = traceback.extract_tb(exc.__traceback__)[-5:]
    where = " <- ".join(f"{Path(f.filename).name}:{f.lineno}:{f.name}" for f in reversed(frames))
    return f"{type(exc).__name__} at {where or '-'}"


def _loc(parts: tuple[object, ...]) -> str:
    """422 위치. 칸 이름 · 순번만 남기고, 식별자가 아닌 사전 키(사용자가 보낸 값)는 <key> 로.

    식별자 모양의 사전 키(예: `private_token`)는 칸 이름과 구분하지 않고 남긴다. 이 서비스의
    입력에서 사용자 원문이 사전 키가 되는 경우는 문장(공백 · 한글)이라 여기서 걸러진다.
    """
    return ".".join(
        str(p) if isinstance(p, int) or _IDENTIFIER.fullmatch(str(p)) else "<key>" for p in parts
    )


# 라우터가 만드는 HTTP 오류의 메시지. 없는 상태 코드는 HTTP 표준 문구를 쓴다
_HTTP_ERROR_MESSAGES = {
    status.HTTP_404_NOT_FOUND: "요청한 경로를 찾을 수 없습니다.",
    status.HTTP_405_METHOD_NOT_ALLOWED: "허용되지 않는 요청 방식입니다.",
}


def error_response(
    status_code: int,
    code: str,
    message: str,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    """공통 에러 본문을 만든다. `request_log` 미들웨어의 500 도 이것을 쓴다."""
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": message}},
        headers=headers,
    )


def _remember_code(request: Request, code: str) -> None:
    """요청 로그가 상태 코드 옆에 에러 코드도 남기게 한다 (request_log.py 가 읽음)."""
    request.state.error_code = code


def internal_error_response() -> JSONResponse:
    return error_response(
        status.HTTP_500_INTERNAL_SERVER_ERROR, "INTERNAL_ERROR", "서버 오류가 발생했습니다."
    )


def _http_error(status_code: int) -> tuple[str, str]:
    """상태 코드로 (code, message) 를 만든다. 404 → NOT_FOUND, 405 → METHOD_NOT_ALLOWED."""
    try:
        phrase = HTTPStatus(status_code).phrase
    except ValueError:
        phrase = "HTTP Error"
    code = re.sub(r"[^A-Z0-9]+", "_", phrase.upper()).strip("_")
    return code, _HTTP_ERROR_MESSAGES.get(status_code, phrase)


async def _app_error_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, AppError)
    _remember_code(request, exc.code)
    return error_response(exc.status_code, exc.code, exc.message)


async def _validation_error_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)
    _remember_code(request, "VALIDATION_ERROR")
    # 어느 칸이 어떤 종류로 틀렸는지만 남긴다. 입력값(input)은 사용자 원문일 수 있어 남기지 않는다
    where = [(_loc(tuple(e.get("loc", ()))), e.get("type")) for e in exc.errors()]
    logger.info("validation_error fields=%s", where)
    return error_response(
        status.HTTP_422_UNPROCESSABLE_CONTENT, "VALIDATION_ERROR", "요청 형식이 올바르지 않습니다."
    )


async def _http_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """라우터가 만드는 404 · 405 등. 405 의 Allow 처럼 원래 헤더는 그대로 넘긴다."""
    assert isinstance(exc, HTTPException)
    code, message = _http_error(exc.status_code)
    _remember_code(request, code)
    return error_response(exc.status_code, code, message, headers=exc.headers)


async def _unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.error(
        "unhandled_error path=%s error=%s", log_safe(request.url.path), exception_summary(exc)
    )
    return internal_error_response()


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AppError, _app_error_handler)
    app.add_exception_handler(RequestValidationError, _validation_error_handler)
    app.add_exception_handler(HTTPException, _http_error_handler)
    app.add_exception_handler(Exception, _unhandled_error_handler)
