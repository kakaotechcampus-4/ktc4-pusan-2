"""AI 호출 공용 부품 (`core/ai_http.py`)."""

import httpx
import pytest

from pitch_coach_backend.core import ai_http
from pitch_coach_backend.core.config import settings


@pytest.mark.anyio
async def test_ai_base_url_may_end_with_a_slash(monkeypatch: pytest.MonkeyPatch):
    seen: list[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, json={})

    monkeypatch.setattr(settings, "ai_base_url", "http://ai:8000/")
    async with ai_http.ai_client(1.0, transport=httpx.MockTransport(respond)) as client:
        await client.post("/takes/review", json={})

    assert seen == ["http://ai:8000/takes/review"]


@pytest.mark.parametrize(
    ("status", "retryable"),
    [(408, True), (429, True), (500, True), (503, True), (400, False), (404, False), (422, False)],
)
def test_retryable_status(status: int, retryable: bool):
    assert ai_http.retryable_status(status) is retryable


@pytest.mark.parametrize(
    ("body", "code"),
    [
        ({"error": {"code": "LLM_TIMEOUT"}}, "LLM_TIMEOUT"),
        ({"error": {"code": "사용자 원문이 돌아온 값"}}, "UNKNOWN"),
        ({"error": "문자열"}, "UNKNOWN"),
        ([], "UNKNOWN"),
    ],
)
def test_error_code_keeps_only_contract_codes(body: object, code: str):
    assert ai_http.ai_error_code(httpx.Response(400, json=body)) == code


def test_error_code_of_a_body_that_is_not_json():
    assert ai_http.ai_error_code(httpx.Response(500, content=b"<html>")) == "UNKNOWN"
