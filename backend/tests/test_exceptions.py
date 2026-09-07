"""에러 응답 형식 계약. 실제 라우터 대신 최소 앱으로 핸들러만 검증한다."""

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


@pytest.fixture
def probe(monkeypatch: pytest.MonkeyPatch) -> Generator[TestClient]:
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

    with TestClient(app) as c:
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
