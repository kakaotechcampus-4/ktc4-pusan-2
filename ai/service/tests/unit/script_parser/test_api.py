"""대본 파싱 엔드포인트 `POST /scripts/parse` (`api/script_parser.py`).

응답 · 에러 코드 · meta · 마감 · 로그를 본다.

LLM 은 가짜다. 라우터의 LLM 만들기(`get_llm_factory`)를 테스트에서 바꿔 끼운다.
"""

import json
import logging
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from pitch_coach_ai.api import script_parser as api
from pitch_coach_ai.common.errors import (
    LlmCallFailed,
    LlmInvalidOutput,
    LlmTimeout,
    LlmUnavailable,
)
from pitch_coach_ai.features.script_parser import core
from pitch_coach_ai.features.script_parser.schemas import LLMOutput
from pitch_coach_ai.main import create_app
from pitch_coach_ai.settings import LLM_ROLES, Settings

EXAMPLES = Path(core.__file__).resolve().parent / "examples"
MODEL = "openai/gpt-6-luna"
FULL = Settings(
    _env_file=None,
    openai_base_url="https://llm.example/v1",
    openai_api_key="sk-test-secret",
    openai_model=MODEL,
)
EMPTY = Settings(_env_file=None)


def load(case: str, name: str) -> Any:
    return json.loads((EXAMPLES / case / name).read_text(encoding="utf-8"))


class FakeLLM:
    """정해진 LLM 출력(또는 예외)을 돌려준다. 성공한 호출 뒤에만 `responded_model` 을 둔다."""

    def __init__(self, output: dict | Exception) -> None:
        self.output = output
        self.calls = 0
        self.responded_model: str | None = None

    def invoke(self, messages: Any) -> LLMOutput:
        self.calls += 1
        if isinstance(self.output, Exception):
            raise self.output
        self.responded_model = "openai/gpt-6-luna-2026-06"
        return LLMOutput.model_validate(self.output)


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


# ── 성공 ──────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("case", ["success", "no_target", "fail"])
def test_example_request_returns_the_example_response_with_the_responding_model(
    make_client, case: str
) -> None:
    res = make_client(FakeLLM(load(case, "llm_output.json"))).post(
        "/scripts/parse", json=load(case, "request.json")
    )
    assert res.status_code == 200
    body = res.json()
    expected = load(case, "response.json")
    assert body["meta"]["model"] == "openai/gpt-6-luna-2026-06"
    body["meta"]["model"] = expected["meta"]["model"] = None
    assert body == expected


def test_highlights_are_start_end_objects_in_code_points(make_client) -> None:
    body = (
        make_client(FakeLLM(load("success", "llm_output.json")))
        .post("/scripts/parse", json=load("success", "request.json"))
        .json()
    )
    for s in body["slides"]:
        assert [s["script"][h["start"] : h["end"]] for h in s["highlights"]] == s["keywords"]


def test_unknown_request_fields_are_ignored(make_client) -> None:
    req = {**load("success", "request.json"), "pitch_id": 7}
    res = make_client(FakeLLM(load("success", "llm_output.json"))).post("/scripts/parse", json=req)
    assert res.status_code == 200


def test_llm_gets_the_25_second_budget_its_role_and_output_schema(make_client) -> None:
    client = make_client(FakeLLM(load("success", "llm_output.json")))
    before = time.monotonic()
    client.post("/scripts/parse", json=load("success", "request.json"))
    ((schema, role, deadline),) = client.calls
    assert schema is LLMOutput
    assert role == "script_parser"
    assert before + api.BUDGET_SECONDS <= deadline <= time.monotonic() + api.BUDGET_SECONDS


def test_role_uses_the_measured_output_limit_and_low_reasoning() -> None:
    role = LLM_ROLES["script_parser"]
    assert (role.bundle, role.max_output_tokens, role.reasoning_effort) == ("default", 2000, "low")


# ── 입력 오류 ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("text", ["   \n\t", "[1]\n[2]\n슬라이드 3:"])
def test_script_without_text_is_422_empty_script_without_calling_the_llm(
    make_client, text: str
) -> None:
    llm = FakeLLM(load("success", "llm_output.json"))
    res = make_client(llm).post("/scripts/parse", json={"script_text": text})
    assert res.status_code == 422
    assert res.json()["error"]["code"] == "EMPTY_SCRIPT"
    assert llm.calls == 0


def test_empty_script_is_422_even_without_model_settings(make_client) -> None:
    res = make_client(settings=EMPTY).post("/scripts/parse", json={"script_text": "[1]\n"})
    assert res.status_code == 422
    assert res.json()["error"]["code"] == "EMPTY_SCRIPT"


def test_script_with_only_title_lines_is_422_empty_script_after_the_llm(make_client) -> None:
    llm = FakeLLM(
        {
            "preface_title_lines": [],
            "slides": [{"slide_number": 1, "title_lines": ["표지"], "keywords": []}],
            "terms": [],
        }
    )
    res = make_client(llm).post("/scripts/parse", json={"script_text": "[1] 표지"})
    assert res.status_code == 422
    assert res.json()["error"]["code"] == "EMPTY_SCRIPT"
    assert llm.calls == 1


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"script_text": "가" * 50_001},
        {"script_text": "[1] 안녕", "target_ms": 0},
        {"script_text": 3},
    ],
    ids=["no_text", "over_50000", "target_zero", "not_string"],
)
def test_malformed_request_is_422_validation_error(make_client, body: dict) -> None:
    llm = FakeLLM(load("success", "llm_output.json"))
    res = make_client(llm).post("/scripts/parse", json=body)
    assert res.status_code == 422
    assert res.json()["error"]["code"] == "VALIDATION_ERROR"
    assert llm.calls == 0


# ── LLM 실패 ──────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("error", "status", "code"),
    [
        (LlmTimeout(), 504, "LLM_TIMEOUT"),
        (LlmCallFailed(), 502, "LLM_ERROR"),
        (LlmInvalidOutput(), 502, "LLM_INVALID_OUTPUT"),
        (LlmUnavailable(), 503, "LLM_UNAVAILABLE"),
    ],
)
def test_llm_failure_becomes_its_status_code(make_client, error, status: int, code: str) -> None:
    res = make_client(FakeLLM(error)).post("/scripts/parse", json=load("success", "request.json"))
    assert res.status_code == status
    assert res.json()["error"]["code"] == code


def test_without_model_settings_it_is_503_llm_unavailable(make_client) -> None:
    res = make_client(settings=EMPTY).post("/scripts/parse", json=load("success", "request.json"))
    assert res.status_code == 503
    assert res.json()["error"]["code"] == "LLM_UNAVAILABLE"


# ── 로그 ──────────────────────────────────────────────────────────────────────


def test_log_has_length_slides_and_status_but_no_script_keywords_or_terms(
    make_client, log_records: list[logging.LogRecord]
) -> None:
    req = load("success", "request.json")
    make_client(FakeLLM(load("success", "llm_output.json"))).post("/scripts/parse", json=req)
    lines = [r.getMessage() for r in log_records if r.name == "pitch_coach_ai.script_parser"]
    assert len(lines) == 1
    assert lines[0].startswith(
        f"script_parser chars={len(req['script_text'])} target_ms={req['target_ms']} "
        "slides=2 status=success code=- "
    )
    text = "\n".join(r.getMessage() for r in log_records if r.name.startswith("pitch_coach_ai"))
    response = load("success", "response.json")
    for word in ["안녕하세요", *response["terms"], *response["slides"][0]["keywords"]]:
        assert word not in text


def test_error_log_has_the_code(make_client, log_records: list[logging.LogRecord]) -> None:
    make_client(FakeLLM(LlmTimeout())).post("/scripts/parse", json=load("success", "request.json"))
    (line,) = [r.getMessage() for r in log_records if r.name == "pitch_coach_ai.script_parser"]
    assert " slides=- status=- code=LLM_TIMEOUT " in line


def test_empty_script_log_line_has_the_code_and_no_target(
    make_client, log_records: list[logging.LogRecord]
) -> None:
    make_client(FakeLLM(load("success", "llm_output.json"))).post(
        "/scripts/parse", json={"script_text": "[1]\n"}
    )
    (line,) = [r.getMessage() for r in log_records if r.name == "pitch_coach_ai.script_parser"]
    assert line.startswith("script_parser chars=4 target_ms=- slides=- status=- code=EMPTY_SCRIPT ")


# ── meta.model · LLM 만들기 · 요청 ID ─────────────────────────────────────────


class _Silent(FakeLLM):
    """응답한 모델 이름을 알려 주지 않는 LLM (콜백이 모델 이름을 받지 못한 경우)."""

    def invoke(self, messages: Any) -> LLMOutput:
        result = super().invoke(messages)
        self.responded_model = None
        return result


def test_meta_model_falls_back_to_the_configured_model(make_client) -> None:
    body = (
        make_client(_Silent(load("success", "llm_output.json")))
        .post("/scripts/parse", json=load("success", "request.json"))
        .json()
    )
    assert body["meta"]["model"] == MODEL


def test_default_factory_builds_the_llm_with_role_deadline_and_app_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple] = []

    def fake_structured_llm(schema, model, deadline, *, app_settings):
        calls.append((schema, model, deadline, app_settings))
        return FakeLLM(load("success", "llm_output.json"))

    monkeypatch.setattr(api, "structured_llm", fake_structured_llm)
    with TestClient(create_app(FULL)) as client:
        before = time.monotonic()
        res = client.post("/scripts/parse", json=load("success", "request.json"))
    assert res.status_code == 200
    ((schema, model, deadline, app_settings),) = calls
    assert (schema, model, app_settings) == (LLMOutput, "script_parser", FULL)
    assert deadline >= before + api.BUDGET_SECONDS


@pytest.mark.parametrize(
    ("body", "status"),
    [(load("success", "request.json"), 200), ({"script_text": "[1]\n"}, 422)],
    ids=["ok", "empty_script"],
)
def test_request_id_comes_back_on_success_and_error(make_client, body: dict, status: int) -> None:
    client = make_client(FakeLLM(load("success", "llm_output.json")))
    res = client.post("/scripts/parse", json=body, headers={"X-Request-ID": "req-abc"})
    assert res.status_code == status
    assert res.headers["X-Request-ID"] == "req-abc"
