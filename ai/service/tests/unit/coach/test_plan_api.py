"""코칭 계획 엔드포인트 `POST /coach/plan` (`api/coach.py`): 기본 계획 · 422 · meta · 마감 · 로그.

LLM 은 가짜다. 라우터의 LLM 만들기(`get_llm_factory`)를 테스트에서 바꿔 끼운다.
"""

import logging
import time
from collections.abc import Callable, Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from pitch_coach_ai.api import coach as api
from pitch_coach_ai.common.errors import LlmCallFailed, LlmInvalidOutput, LlmTimeout
from pitch_coach_ai.main import create_app
from pitch_coach_ai.settings import Settings

from .test_planner import GAZE_3, FakeLLM, draft, request

MODEL = "openai/gpt-6-luna"
FULL = Settings(
    _env_file=None,
    openai_base_url="https://llm.example/v1",
    openai_api_key="sk-test-secret",
    openai_model=MODEL,
)


class _Answering(FakeLLM):
    """성공한 호출 뒤에만 `responded_model` 을 기록한다 (서비스 LLM 객체처럼)."""

    responded_model: str | None = None

    def invoke(self, messages):
        result = super().invoke(messages)
        self.responded_model = "openai/gpt-6-luna-2026-06"
        return result


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


def test_plan_returns_the_llm_plan_and_meta(make_client) -> None:
    focus = {"type": "TIME", "slide_number": 3, "weight": 1.5, "why": "미션"}
    llm = _Answering(draft(focus=[focus], relax=[GAZE_3]))
    res = make_client(llm).post("/coach/plan", json=request())
    assert res.status_code == 200
    body = res.json()
    assert body["fallback_reason"] is None
    assert [(f["area"], f["slide_number"]) for f in body["coaching_plan"]["focus"]] == [("TIME", 3)]
    assert [r["slide_number"] for r in body["coaching_plan"]["relax"]] == [3]
    assert body["meta"]["model"] == "openai/gpt-6-luna-2026-06"
    assert body["meta"]["feature_version"].startswith("coach-")
    assert len(llm.calls) == 1


def test_without_model_settings_it_is_the_default_plan_200(make_client) -> None:
    res = make_client(settings=Settings(_env_file=None)).post("/coach/plan", json=request())
    assert res.status_code == 200
    body = res.json()
    assert body["fallback_reason"] == "LLM_UNAVAILABLE"
    assert body["coaching_plan"] == {"focus": [], "relax": [], "max_interventions": None}
    assert body["meta"]["model"] is None


@pytest.mark.parametrize(
    ("error", "reason"),
    [
        (LlmTimeout(), "LLM_TIMEOUT"),
        (LlmCallFailed(), "LLM_ERROR"),
        (LlmInvalidOutput(), "LLM_INVALID_OUTPUT"),
    ],
)
def test_llm_failure_is_the_default_plan_with_the_reason(make_client, error, reason) -> None:
    res = make_client(FakeLLM(error)).post("/coach/plan", json=request())
    assert res.status_code == 200
    assert res.json()["fallback_reason"] == reason
    assert res.json()["meta"]["model"] is None


@pytest.mark.parametrize(("length", "status"), [(500, 200), (501, 422)])
def test_mission_description_limit_is_500_characters(make_client, length, status) -> None:
    mission = {"mission_id": "m1", "area": "GAZE", "slide_number": 3, "description": "가" * length}
    res = make_client(FakeLLM(draft())).post("/coach/plan", json=request(missions=[mission]))
    assert res.status_code == status
    if status == 422:
        assert res.json() == {
            "error": {"code": "VALIDATION_ERROR", "message": "요청 형식이 올바르지 않습니다."}
        }


def test_llm_answer_that_fails_validation_is_the_default_plan_without_model(
    make_client, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 모델은 답했지만 초안 검증이 실패했다 — 기본 계획이고 meta.model 은 null
    def boom(*args, **kwargs):
        raise ValueError("검증 실패")

    monkeypatch.setattr("pitch_coach_ai.features.coach.planner.validate_draft", boom)
    llm = _Answering(draft(relax=[GAZE_3]))
    body = make_client(llm).post("/coach/plan", json=request()).json()
    assert llm.responded_model is not None
    assert body["fallback_reason"] == "LLM_INVALID_OUTPUT"
    assert body["coaching_plan"] == {"focus": [], "relax": [], "max_interventions": None}
    assert body["meta"]["model"] is None


def test_plan_failure_log_has_no_exception_message(
    make_client, log_records: list[logging.LogRecord]
) -> None:
    llm = FakeLLM(LlmTimeout("대본 원문이 섞인 예외 메시지"))
    make_client(llm).post("/coach/plan", json=request())
    records = [r for r in log_records if r.name.startswith("pitch_coach_ai.features.coach")]
    assert records and all(r.exc_info is None for r in records)
    assert "대본 원문" not in " ".join(r.getMessage() for r in records)


def test_plan_llm_gets_the_25_second_budget_and_its_role(make_client) -> None:
    client = make_client(FakeLLM(draft()))
    before = time.monotonic()
    client.post("/coach/plan", json=request())
    ((role, deadline),) = client.calls
    assert role == "coach.plan"
    assert (
        before + api.PLAN_BUDGET_SECONDS <= deadline <= time.monotonic() + api.PLAN_BUDGET_SECONDS
    )


def test_plan_log_has_counts_but_no_script_text(
    make_client, log_records: list[logging.LogRecord]
) -> None:
    make_client(FakeLLM(LlmTimeout())).post("/coach/plan", json=request())
    lines = [r.getMessage() for r in log_records if r.name == "pitch_coach_ai.coach"]
    assert len(lines) == 1
    slides = len(request()["scripts"])
    assert lines[0].startswith(f"coach_plan slides={slides} missions=0 fallback=LLM_TIMEOUT ")
    assert "매출" not in "\n".join(
        r.getMessage() for r in log_records if r.name.startswith("pitch_coach_ai")
    )
