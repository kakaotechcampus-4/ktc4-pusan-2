"""공통 예외와 핸들러.

도메인 예외는 module/<name>/exception.py 에서 아래 클래스를 상속해 정의한다.

    class UserNotFound(NotFoundException):
        message = "사용자를 찾을 수 없습니다."

모든 에러 응답은 같은 형태로 나간다 (프론트엔드와의 계약).

    {"code": "NOT_FOUND", "message": "...", "detail": null}

도메인 예외만이 아니라 라우터가 만드는 404·405 와 처리되지 않은 예외(500) 도
같은 형태로 바꾼다. 프론트엔드가 모든 에러에서 code 로 분기할 수 있어야 한다.
"""

import logging
import math
import re
from http import HTTPStatus
from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from pitch_coach_backend.core.logging import sqlstate
from pitch_coach_backend.core.validation import MAX_JSON_DEPTH

logger = logging.getLogger(__name__)


class AppException(Exception):
    status_code: int = status.HTTP_400_BAD_REQUEST
    code: str = "BAD_REQUEST"
    message: str = "잘못된 요청입니다."

    def __init__(self, message: str | None = None, *, detail: Any = None) -> None:
        if message is not None:
            self.message = message
        self.detail = detail
        super().__init__(self.message)


class UnauthorizedException(AppException):
    status_code = status.HTTP_401_UNAUTHORIZED
    code = "UNAUTHORIZED"
    message = "인증이 필요합니다."


class ForbiddenException(AppException):
    status_code = status.HTTP_403_FORBIDDEN
    code = "FORBIDDEN"
    message = "권한이 없습니다."


class NotFoundException(AppException):
    status_code = status.HTTP_404_NOT_FOUND
    code = "NOT_FOUND"
    message = "리소스를 찾을 수 없습니다."


class ConflictException(AppException):
    status_code = status.HTTP_409_CONFLICT
    code = "CONFLICT"
    message = "이미 존재하는 리소스입니다."


# 라우터·미들웨어가 만드는 HTTP 오류의 사용자 메시지.
# 없는 status 는 HTTP 표준 문구를 그대로 쓴다.
_HTTP_ERROR_MESSAGES = {
    status.HTTP_404_NOT_FOUND: "요청한 경로를 찾을 수 없습니다.",
    status.HTTP_405_METHOD_NOT_ALLOWED: "허용되지 않는 요청 방식입니다.",
}


def _http_error(status_code: int) -> tuple[str, str]:
    """status 로 (code, message) 를 만든다.

    code 는 HTTP 표준 문구에서 뽑는다 (404 -> NOT_FOUND, 405 -> METHOD_NOT_ALLOWED).
    위 예외 클래스들의 code 와 같은 이름이 되므로 프론트엔드는 출처를 구분할 필요가 없다.
    """
    try:
        phrase = HTTPStatus(status_code).phrase
    except ValueError:
        phrase = "HTTP Error"
    code = re.sub(r"[^A-Z0-9]+", "_", phrase.upper()).strip("_")
    return code, _HTTP_ERROR_MESSAGES.get(status_code, phrase)


def _error_response(
    status_code: int,
    code: str,
    message: str,
    detail: Any = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    merged = dict(headers or {})
    if status_code == status.HTTP_401_UNAUTHORIZED:
        merged.setdefault("WWW-Authenticate", "Bearer")
    return JSONResponse(
        status_code=status_code,
        content={"code": code, "message": message, "detail": jsonable_encoder(detail)},
        headers=merged or None,
    )


async def app_exception_handler(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, AppException)
    return _error_response(exc.status_code, exc.code, exc.message, exc.detail)


def _json_safe_values(value: Any) -> Any:
    """검증 오류의 비유한 수·잘못된 유니코드도 JSON으로 응답할 수 있게 한다."""
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)
    if isinstance(value, str):
        return value.encode("utf-8", errors="backslashreplace").decode("utf-8")
    if isinstance(value, dict):
        return {_json_safe_values(key): _json_safe_values(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe_values(item) for item in value]
    return value


def _deeper_than(value: Any, limit: int) -> bool:
    """객체 · 배열이 `limit` 단계보다 깊게 겹쳤나. 재귀 한도에 걸리지 않게 반복으로 센다."""
    stack = [(value, 0)]
    while stack:
        current, depth = stack.pop()
        if isinstance(current, dict | list):
            if depth >= limit:
                return True
            items = current.values() if isinstance(current, dict) else current
            stack.extend((item, depth + 1) for item in items)
    return False


def _without_deep_input(error: dict[str, Any]) -> dict[str, Any]:
    # 깊이 상한(`MAX_JSON_DEPTH`)으로 거절한 값은 다시 JSON 으로 바꾸다 재귀 한도에 걸린다
    if _deeper_than(error.get("input"), MAX_JSON_DEPTH):
        return {key: value for key, value in error.items() if key != "input"}
    return error


async def validation_exception_handler(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)
    # 검증에서 거절한 값이 detail에 다시 들어가도 응답 직렬화가 실패하지 않게 한다.
    errors = [_without_deep_input(error) for error in exc.errors()]
    return _error_response(
        status.HTTP_422_UNPROCESSABLE_CONTENT,
        "VALIDATION_ERROR",
        "요청 형식이 올바르지 않습니다.",
        _json_safe_values(jsonable_encoder(errors)),
    )


async def http_exception_handler(_: Request, exc: Exception) -> JSONResponse:
    """라우터가 만드는 404·405 등. 405 의 Allow 처럼 원래 헤더는 그대로 넘긴다."""
    assert isinstance(exc, HTTPException)
    code, message = _http_error(exc.status_code)
    return _error_response(exc.status_code, code, message, headers=exc.headers)


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """처리되지 않은 예외. 원문을 포함할 수 있는 본문·traceback 대신 안전한 진단값만 남긴다."""
    logger.error(
        "처리되지 않은 예외: %s %s error=%s sqlstate=%s",
        request.method,
        request.url.path,
        type(exc).__name__,
        sqlstate(exc),
    )
    return _error_response(
        status.HTTP_500_INTERNAL_SERVER_ERROR,
        "INTERNAL_SERVER_ERROR",
        "서버 오류가 발생했습니다.",
    )


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AppException, app_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.add_exception_handler(HTTPException, http_exception_handler)
    app.add_exception_handler(Exception, unhandled_exception_handler)
