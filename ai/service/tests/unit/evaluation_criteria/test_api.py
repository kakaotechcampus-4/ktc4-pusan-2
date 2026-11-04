"""평가 기준 분석 엔드포인트 `POST /evaluation-criteria/parse` (`api/evaluation_criteria.py`):
응답 · meta · LLM 실패 상태 코드 · 422 · 마감 · 로그.

LLM 은 가짜다. 라우터의 LLM 만들기(`get_llm_factory`)를 테스트에서 바꿔 끼운다.
"""

import logging
import time
from collections.abc import Callable, Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from pitch_coach_ai.api import evaluation_criteria as api
from pitch_coach_ai.common.errors import (
    LlmCallFailed,
    LlmInvalidOutput,
    LlmTimeout,
    LlmUnavailable,
)
from pitch_coach_ai.common.llm import StructuredLlm
from pitch_coach_ai.features.evaluation_criteria.schemas import EvaluationCriteriaAnalysis
from pitch_coach_ai.features.evaluation_criteria.version import FEATURE_VERSION, SCHEMA_VERSION
from pitch_coach_ai.main import create_app
from pitch_coach_ai.settings import Settings

from .fakes import EMPTY_VALUES, FakeLLM, analysis

PATH = "/evaluation-criteria/parse"
TEXT = "추임새는 5번 이하로 하고, 청중을 자주 보면서 말하고 싶어요."
FULL = Settings(
    _env_file=None,
    openai_base_url="https://llm.example/v1",
    openai_api_key="sk-test-secret",
    openai_model="openai/gpt-6-luna",
)


class _Answering(FakeLLM):
    """성공한 호출 뒤에만 `responded_model` 을 기록한다 (서비스 LLM 객체처럼)."""

    responded_model: str | None = None

    def invoke(self, messages):
        result = super().invoke(messages)
        self.responded_model = "openai/gpt-6-luna-2026-06"
        return result


def _answer() -> EvaluationCriteriaAnalysis:
    display = ["추임새 5번 이하", "청중을 자주 보기"]
    return analysis(
        display_criteria=display,
        values={"filler": display[0], "gaze": display[1]},
        targets=[
            {"display": display[0], "metric": "filler_count", "operator": "LTE", "value": 5},
            # 표시 기준에 없는 문장의 목표는 코어 검사가 뺀다
            {"display": "없는 문장", "metric": "cpm", "operator": "LTE", "value": 300},
        ],
    )


@pytest.fixture
def make_client() -> Iterator[Callable[..., TestClient]]:
    opened: list[TestClient] = []

    def make(llm: Any = None, settings: Settings = FULL) -> TestClient:
        app = create_app(settings)
        calls: list[tuple[str, float]] = []
        if llm is not None:

            def factory(schema, role, deadline, s):
                calls.append((role, deadline))
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


def test_parse_returns_the_checked_analysis_and_meta(make_client) -> None:
    llm = _Answering(_answer())
    res = make_client(llm).post(PATH, json={"standard_text": TEXT})
    assert res.status_code == 200
    body = res.json()
    assert body["display_criteria"] == ["추임새 5번 이하", "청중을 자주 보기"]
    assert body["values"] == {
        **EMPTY_VALUES,
        "filler": "추임새 5번 이하",
        "gaze": "청중을 자주 보기",
    }
    assert body["targets"] == [
        {"display": "추임새 5번 이하", "metric": "filler_count", "operator": "LTE", "value": 5.0}
    ]
    assert body["excluded"] == []
    assert body["meta"] == {
        "schema_version": SCHEMA_VERSION,
        "feature_version": FEATURE_VERSION,
        "model": "openai/gpt-6-luna-2026-06",
    }
    assert len(llm.calls) == 1
    assert llm.calls[0][1] == ("user", TEXT)


def test_without_model_settings_it_is_503_llm_unavailable(make_client) -> None:
    res = make_client(settings=Settings(_env_file=None)).post(PATH, json={"standard_text": TEXT})
    assert res.status_code == 503
    assert res.json() == {"error": {"code": "LLM_UNAVAILABLE", "message": LlmUnavailable.message}}


@pytest.mark.parametrize(
    ("error", "status"),
    [(LlmTimeout(), 504), (LlmCallFailed(), 502), (LlmInvalidOutput(), 502)],
)
def test_llm_failure_is_the_common_error_body(make_client, error, status) -> None:
    res = make_client(FakeLLM(error=error)).post(PATH, json={"standard_text": TEXT})
    assert res.status_code == status
    assert res.json() == {"error": {"code": error.code, "message": error.message}}


class _Runnable:
    """LLM 클라이언트 자리. 50자 넘는 표시 기준을 출력 스키마로 검증한다(실제 구조화 출력처럼)."""

    def invoke(self, input: Any, config: Any = None) -> EvaluationCriteriaAnalysis:  # noqa: A002
        return EvaluationCriteriaAnalysis.model_validate(
            {"display_criteria": ["가" * 51], "values": EMPTY_VALUES, "targets": [], "excluded": []}
        )


def test_display_criterion_over_50_chars_is_502_llm_invalid_output(make_client) -> None:
    # 자르지 않고 스키마 위반으로 본다 (기능 문서 6절 2번 · 10절). 서비스 LLM 객체가 502 로 바꾼다
    llm = StructuredLlm(_Runnable(), role=api.ROLE, timeout_seconds=5, max_retries=1)
    res = make_client(llm).post(PATH, json={"standard_text": TEXT})
    assert res.status_code == 502
    assert res.json()["error"]["code"] == "LLM_INVALID_OUTPUT"


@pytest.mark.parametrize(
    ("text", "status"),
    [
        ("", 422),
        ("   ", 422),
        ("가" * 2000, 200),
        (" " + "가" * 2000 + " ", 200),
        ("가" * 2001, 422),
    ],
)
def test_standard_text_is_1_to_2000_characters_after_strip(make_client, text, status) -> None:
    llm = FakeLLM()
    res = make_client(llm).post(PATH, json={"standard_text": text})
    assert res.status_code == status
    if status == 422:
        assert res.json()["error"]["code"] == "VALIDATION_ERROR"
        assert llm.calls == []  # LLM 을 부르지 않는다


def test_parse_llm_gets_the_25_second_budget_and_its_role(make_client) -> None:
    client = make_client(FakeLLM())
    before = time.monotonic()
    client.post(PATH, json={"standard_text": TEXT})
    ((role, deadline),) = client.calls
    assert role == "evaluation_criteria"
    assert before + api.BUDGET_SECONDS <= deadline <= time.monotonic() + api.BUDGET_SECONDS


def test_log_has_length_and_target_count_but_no_criterion_text(
    make_client, log_records: list[logging.LogRecord]
) -> None:
    make_client(_Answering(_answer())).post(PATH, json={"standard_text": TEXT})
    lines = [r.getMessage() for r in log_records if r.name == "pitch_coach_ai.evaluation_criteria"]
    assert len(lines) == 1
    assert lines[0].startswith(f"evaluation_criteria chars={len(TEXT)} targets=1 code=- ")
    assert "추임새" not in "\n".join(
        r.getMessage() for r in log_records if r.name.startswith("pitch_coach_ai")
    )


def test_failure_log_has_the_error_code(make_client, log_records: list[logging.LogRecord]) -> None:
    make_client(FakeLLM(error=LlmTimeout())).post(PATH, json={"standard_text": TEXT})
    lines = [r.getMessage() for r in log_records if r.name == "pitch_coach_ai.evaluation_criteria"]
    assert len(lines) == 1
    assert lines[0].startswith(f"evaluation_criteria chars={len(TEXT)} targets=- code=LLM_TIMEOUT ")
