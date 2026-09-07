"""공통 예외와 핸들러.

도메인 예외는 module/<name>/exception.py 에서 아래 클래스를 상속해 정의한다.

    class UserNotFound(NotFoundException):
        message = "사용자를 찾을 수 없습니다."

모든 에러 응답은 같은 형태로 나간다 (프론트엔드와의 계약).

    {"code": "NOT_FOUND", "message": "...", "detail": null}
"""

from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


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


def _error_response(status_code: int, code: str, message: str, detail: Any = None) -> JSONResponse:
    is_unauthorized = status_code == status.HTTP_401_UNAUTHORIZED
    headers = {"WWW-Authenticate": "Bearer"} if is_unauthorized else None
    return JSONResponse(
        status_code=status_code,
        content={"code": code, "message": message, "detail": jsonable_encoder(detail)},
        headers=headers,
    )


async def app_exception_handler(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, AppException)
    return _error_response(exc.status_code, exc.code, exc.message, exc.detail)


async def validation_exception_handler(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)
    return _error_response(
        status.HTTP_422_UNPROCESSABLE_CONTENT,
        "VALIDATION_ERROR",
        "요청 형식이 올바르지 않습니다.",
        exc.errors(),
    )


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AppException, app_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
