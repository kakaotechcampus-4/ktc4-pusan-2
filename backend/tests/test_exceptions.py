"""에러 응답 형식 계약. 실제 라우터 대신 최소 앱으로 핸들러만 검증한다."""

import logging
from collections.abc import Generator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel

from pitch_coach_backend.core.exceptions import (
    AppException,
    NotFoundException,
    UnauthorizedException,
    register_exception_handlers,
)


class _Body(BaseModel):
    n: int


class _UserNotFound(NotFoundException):
    message = "사용자를 찾을 수 없습니다."


def _build_app() -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/notfound")
    def _notfound() -> None:
        raise _UserNotFound()

    @app.get("/unauthorized")
    def _unauthorized() -> None:
        raise UnauthorizedException()

    @app.get("/custom")
    def _custom() -> None:
        raise AppException("직접 메시지", detail={"field": "x"})

    @app.post("/validate")
    def _validate(body: _Body) -> _Body:
        return body

    @app.get("/boom")
    def _boom() -> None:
        raise RuntimeError("DB 비밀번호가 틀렸습니다")

    return app


@pytest.fixture
def probe() -> Generator[TestClient]:
    with TestClient(_build_app()) as c:
        yield c


@pytest.fixture
def lenient_probe() -> Generator[TestClient]:
    """Starlette 는 500 핸들러를 실행한 뒤 예외를 다시 raise 한다.

    응답 계약만 확인하려면 TestClient 가 그 예외를 삼켜야 한다.
    """
    with TestClient(_build_app(), raise_server_exceptions=False) as c:
        yield c


def test_domain_exception_shape(probe: TestClient) -> None:
    res = probe.get("/notfound")
    assert res.status_code == 404
    assert res.json() == {
        "code": "NOT_FOUND",
        "message": "사용자를 찾을 수 없습니다.",
        "detail": None,
    }


def test_unauthorized_has_www_authenticate(probe: TestClient) -> None:
    res = probe.get("/unauthorized")
    assert res.status_code == 401
    assert res.headers["www-authenticate"] == "Bearer"
    assert res.json()["code"] == "UNAUTHORIZED"


def test_custom_message_and_detail(probe: TestClient) -> None:
    res = probe.get("/custom")
    assert res.status_code == 400
    assert res.json() == {"code": "BAD_REQUEST", "message": "직접 메시지", "detail": {"field": "x"}}


def test_validation_error_same_shape(probe: TestClient) -> None:
    res = probe.post("/validate", json={"n": "abc"})
    body = res.json()
    assert res.status_code == 422
    assert body["code"] == "VALIDATION_ERROR"
    assert body["detail"][0]["loc"] == ["body", "n"]


def test_unknown_path_same_shape(probe: TestClient) -> None:
    """라우터가 만드는 404 도 code 를 갖는다. HTTPException 을 던지지 않아도 발생한다."""
    res = probe.get("/이런-경로-없음")
    assert res.status_code == 404
    assert res.json() == {
        "code": "NOT_FOUND",
        "message": "요청한 경로를 찾을 수 없습니다.",
        "detail": None,
    }


def test_method_not_allowed_same_shape(probe: TestClient) -> None:
    res = probe.post("/notfound")
    assert res.status_code == 405
    assert res.json() == {
        "code": "METHOD_NOT_ALLOWED",
        "message": "허용되지 않는 요청 방식입니다.",
        "detail": None,
    }
    # 405 가 원래 달고 오는 헤더를 핸들러가 버리지 않는다
    assert res.headers["allow"] == "GET"


def test_unhandled_exception_returns_json_without_internals(
    lenient_probe: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.ERROR):
        res = lenient_probe.get("/boom")

    assert res.status_code == 500
    assert res.headers["content-type"].startswith("application/json")
    assert res.json() == {
        "code": "INTERNAL_SERVER_ERROR",
        "message": "서버 오류가 발생했습니다.",
        "detail": None,
    }
    # 내부 예외 내용은 응답이 아니라 서버 로그에만 남는다
    assert "DB 비밀번호" not in res.text
    assert "DB 비밀번호가 틀렸습니다" in caplog.text
