"""리뷰 엔드포인트 `POST /takes/review` (`api/review.py`).

예시 응답 · 기본 결과 · 422 · meta · 마감 · 로그를 본다.

LLM 은 가짜다. 라우터의 LLM 만들기(`get_llm_factory`)를 테스트에서 바꿔 끼운다.
"""

import json
import logging
import time
from collections.abc import Callable, Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from pitch_coach_ai.api import review as api
from pitch_coach_ai.common.errors import LlmCallFailed, LlmInvalidOutput, LlmTimeout
from pitch_coach_ai.features.review.schemas import MAX_MISSIONS
from pitch_coach_ai.main import create_app
from pitch_coach_ai.settings import Settings

from .conftest import EXAMPLES, sample_request
from .test_llm import FakeLLM, full_draft

MODEL = "openai/gpt-6-luna"
FULL = Settings(
    _env_file=None,
    openai_base_url="https://llm.example/v1",
    openai_api_key="sk-test-secret",
    openai_model=MODEL,
)


def load(name: str) -> Any:
    return json.loads((EXAMPLES / name).read_text(encoding="utf-8"))


class _Fixed:
    """llm_output.json 을 돌려주고, 성공한 호출 뒤에만 `responded_model` 을 기록한다."""

    responded_model: str | None = None

    def invoke(self, messages: Any) -> Any:
        self.responded_model = "openai/gpt-6-luna-2026-06"
        return load("llm_output.json")


@pytest.fixture
def make_client() -> Iterator[Callable[..., TestClient]]:
    opened: list[TestClient] = []

    def make(llm: Any = None, settings: Settings = FULL) -> TestClient:
        app = create_app(settings)
        calls: list[tuple[type, str, float]] = []
        if llm is not None:

            def factory(schema, role, deadline, s):
                calls.append((schema, role, deadline))
                return llm

            app.dependency_overrides[api.get_llm_factory] = lambda: factory
        client = TestClient(app)
        client.__enter__()
        opened.append(client)
        client.calls = calls  # type: ignore[attr-defined]
        return client

    yield make
    for c in opened:
        c.__exit__(None, None, None)


def _without_model(body: dict[str, Any]) -> dict[str, Any]:
    return {**body, "meta": {k: v for k, v in body["meta"].items() if k != "model"}}


def test_review_with_the_llm_is_the_example_response_and_responded_model(make_client) -> None:
    res = make_client(_Fixed()).post("/takes/review", json=load("request.json"))
    assert res.status_code == 200
    body = res.json()
    assert body["fallback_reason"] is None
    assert body["meta"]["model"] == "openai/gpt-6-luna-2026-06"
    assert _without_model(body) == _without_model(load("response_llm.json"))


def test_without_model_settings_it_is_the_default_review_200(make_client) -> None:
    res = make_client(settings=Settings(_env_file=None)).post(
        "/takes/review", json=load("request.json")
    )
    assert res.status_code == 200
    assert res.json() == load("response.json")
    assert res.json()["fallback_reason"] == "LLM_UNAVAILABLE"
    assert res.json()["meta"]["model"] is None


@pytest.mark.parametrize(
    ("error", "reason"),
    [
        (LlmTimeout(), "LLM_TIMEOUT"),
        (LlmCallFailed(), "LLM_ERROR"),
        (LlmInvalidOutput(), "LLM_INVALID_OUTPUT"),
    ],
)
def test_llm_failure_is_the_default_review_with_the_reason(make_client, error, reason) -> None:
    res = make_client(FakeLLM(error)).post("/takes/review", json=load("request.json"))
    assert res.status_code == 200
    body = res.json()
    assert body["fallback_reason"] == reason
    assert body["meta"]["model"] is None
    # 숫자 · 상태 · 점수 · 미션 판정은 LLM 과 상관없이 같다 (기본 결과 예시와 같다)
    expected = load("response.json")
    assert {k: v for k, v in body.items() if k != "fallback_reason"} == {
        k: v for k, v in expected.items() if k != "fallback_reason"
    }


def test_llm_without_responded_model_keeps_the_configured_model(make_client) -> None:
    body = make_client(FakeLLM(full_draft)).post("/takes/review", json=sample_request()).json()
    assert body["fallback_reason"] is None
    assert body["meta"]["model"] == MODEL


@pytest.mark.parametrize(("count", "status"), [(MAX_MISSIONS, 200), (MAX_MISSIONS + 1, 422)])
def test_missions_limit_is_20(make_client, count, status) -> None:
    req = sample_request()
    req["missions"] = [{**req["missions"][0], "mission_id": f"m-{i}"} for i in range(count)]
    llm = FakeLLM(full_draft)
    res = make_client(llm).post("/takes/review", json=req)
    assert res.status_code == status
    if status == 422:
        assert res.json() == {
            "error": {"code": "VALIDATION_ERROR", "message": "요청 형식이 올바르지 않습니다."}
        }
        # 형식 오류면 LLM 을 부르지 않는다
        assert llm.messages is None


def test_review_llm_gets_the_25_second_budget_its_role_and_the_draft_schema(make_client) -> None:
    client = make_client(FakeLLM(full_draft))
    before = time.monotonic()
    client.post("/takes/review", json=sample_request())
    ((schema, role, deadline),) = client.calls
    assert schema.__name__ == "ReviewDraft"
    assert role == "review"
    assert before + api.BUDGET_SECONDS <= deadline <= time.monotonic() + api.BUDGET_SECONDS


def test_review_log_has_counts_but_no_review_text(
    make_client, log_records: list[logging.LogRecord]
) -> None:
    make_client(FakeLLM(full_draft)).post("/takes/review", json=sample_request())
    lines = [r.getMessage() for r in log_records if r.name == "pitch_coach_ai.review"]
    assert len(lines) == 1
    req = sample_request()
    assert lines[0].startswith(
        f"review previous_takes={len(req['previous_takes'])} missions={len(req['missions'])}"
        " fallback=- "
    )
    texts = "\n".join(r.getMessage() for r in log_records if r.name.startswith("pitch_coach_ai"))
    assert "LLM 요약" not in texts and "LLM 강점" not in texts


def test_failure_log_has_no_exception_message(
    make_client, log_records: list[logging.LogRecord]
) -> None:
    make_client(FakeLLM(LlmTimeout("리뷰 문장이 섞인 예외 메시지"))).post(
        "/takes/review", json=sample_request()
    )
    records = [r for r in log_records if r.name.startswith("pitch_coach_ai")]
    assert records and all(r.exc_info is None for r in records)
    assert "리뷰 문장" not in " ".join(r.getMessage() for r in records)
