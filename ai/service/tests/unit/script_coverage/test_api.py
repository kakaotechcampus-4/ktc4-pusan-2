"""대본 전달도 엔드포인트(`api/script_coverage.py`): 경로 · 요청 검사 · 장별 실패 · 공통 에러 본문 · meta ·
요청 마감 · 준비 전 503 · 로그.

LLM 은 가짜다. 라우터의 LLM 만들기(`get_llm_factory`)를 테스트에서 바꿔 끼운다.
"""

import json
import logging
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from pitch_coach_ai.api import script_coverage as api
from pitch_coach_ai.common.errors import LlmTimeout
from pitch_coach_ai.common.readiness import Readiness
from pitch_coach_ai.features.script_coverage import core as core_module
from pitch_coach_ai.main import create_app
from pitch_coach_ai.settings import Settings

from .fakes import DeckAwareLLM, KeepFirstVerifier, deck_rubrics
from .shared.slides import SLIDES
from .test_take_core import SaidAllLLM

EXAMPLES = Path(core_module.__file__).parent / "examples"
MODEL = "openai/gpt-6-luna"
NUMBERS = (4, 6, 7)
FULL = Settings(
    _env_file=None,
    openai_base_url="https://llm.example/v1",
    openai_api_key="sk-test-secret",
    openai_model=MODEL,
)


def _script(n: int) -> str:
    return SLIDES[("가상대본1", n)]


def _rubric_request(numbers=NUMBERS, slide_numbers=None) -> dict:
    return {
        "pitch_id": "p-1",
        "script_version_id": "sv-3",
        "slides": [{"slide_number": n, "script": _script(n)} for n in numbers],
        "slide_numbers": slide_numbers,
    }


def _analyze_request(rubric_numbers=NUMBERS, slide_numbers=None) -> dict:
    return {
        "take_id": "take-1",
        # 대본을 그대로 읽은 STT
        "slides": [{"slide_number": n, "stt": _script(n)} for n in NUMBERS],
        "rubrics": [r.model_dump(mode="json") for r in deck_rubrics(rubric_numbers, model=MODEL)],
        "slide_numbers": slide_numbers,
    }


def _script_fakes() -> dict[str, Any]:
    return {s: DeckAwareLLM(s) for s in ("semantic", "final", "unit")}


def _take_fakes() -> dict[str, Any]:
    return {"eval": SaidAllLLM(), "verifier": KeepFirstVerifier()}


class _FailOn:
    """메시지에 `marker` 가 든 호출만 `error` 를 내고 나머지는 `inner` 에 넘긴다."""

    def __init__(self, inner: Any, marker: str, error: BaseException):
        self.inner, self.marker, self.error = inner, marker, error

    def invoke(self, messages):
        if self.marker in messages[1][1]:
            raise self.error
        return self.inner.invoke(messages)


class _Answering:
    """`inner` 에 넘기고, 성공한 호출 뒤에만 `responded_model` 을 기록한다 (서비스 LLM 객체처럼)."""

    def __init__(self, inner: Any, model: str):
        self.inner, self.model = inner, model
        self.responded_model: str | None = None

    def invoke(self, messages):
        result = self.inner.invoke(messages)
        self.responded_model = self.model
        return result


class _Raise:
    def __init__(self, error: BaseException):
        self.error = error

    def invoke(self, messages):
        raise self.error


@pytest.fixture
def make_client() -> Iterator[Callable[..., TestClient]]:
    """가짜 LLM(`fakes`: 칸 → 객체)과 설정으로 앱을 만들고, 형태소 분석기까지 준비된 클라이언트를 준다.

    `fakes` 가 None 이면 라우터의 실제 LLM 만들기를 쓴다(설정이 없으면 LLM_UNAVAILABLE).
    만든 LLM 마다 (역할, 마감)을 `client.calls` 에 남긴다.
    """
    opened: list[TestClient] = []

    def make(fakes: dict[str, Any] | None = None, settings: Settings = FULL) -> TestClient:
        app = create_app(settings)
        calls: list[tuple[str, float]] = []
        if fakes is not None:

            def factory(schema, role, deadline, s):
                calls.append((role, deadline))
                return fakes[role.split(".", 1)[1]]

            app.dependency_overrides[api.get_llm_factory] = lambda: factory
        client = TestClient(app)
        client.__enter__()
        opened.append(client)
        assert app.state.readiness.wait(timeout=60)
        client.calls = calls  # type: ignore[attr-defined]
        return client

    yield make
    for c in opened:
        c.__exit__(None, None, None)


# ── /scripts/rubric ─────────────────────────────────────────────────


def test_rubric_returns_rubrics_and_meta(make_client) -> None:
    res = make_client(_script_fakes()).post("/scripts/rubric", json=_rubric_request())
    assert res.status_code == 200
    body = res.json()
    assert [r["meta"]["slide_number"] for r in body["rubrics"]] == list(NUMBERS)
    assert body["failed"] == []
    assert body["meta"] == {
        "schema_version": "1.0",
        "feature_version": "script_coverage-1.0",
        "model": MODEL,
    }
    # 평가 기준에는 설정한 모델 이름이 남는다 (설정 해시에도 들어간다)
    assert {r["meta"]["llm_model"] for r in body["rubrics"]} == {MODEL}


def test_meta_model_is_the_model_that_actually_answered(make_client) -> None:
    fakes = _script_fakes()
    fakes["final"] = _Answering(fakes["final"], "openai/gpt-6-luna-2026-06")
    body = make_client(fakes).post("/scripts/rubric", json=_rubric_request()).json()
    assert fakes["final"].responded_model == "openai/gpt-6-luna-2026-06"  # 실제로 불렸다
    assert body["meta"]["model"] == "openai/gpt-6-luna-2026-06"


def test_meta_model_is_null_when_no_llm_output_was_used(make_client) -> None:
    # 빈 대본 장뿐이면 LLM 을 부르지 않는다 (architecture.md 5절)
    body = {
        "pitch_id": "p-1",
        "script_version_id": "sv-3",
        "slides": [{"slide_number": 1, "script": ""}],
    }
    res = make_client(_script_fakes()).post("/scripts/rubric", json=body)
    assert res.status_code == 200
    assert res.json()["meta"]["model"] is None


def test_one_failed_slide_goes_to_failed_and_others_succeed(make_client) -> None:
    fakes = _script_fakes()
    fakes["semantic"] = _FailOn(fakes["semantic"], _script(6)[:20], LlmTimeout())
    res = make_client(fakes).post("/scripts/rubric", json=_rubric_request())
    assert res.status_code == 200
    body = res.json()
    assert [r["meta"]["slide_number"] for r in body["rubrics"]] == [4, 7]
    assert body["failed"] == [
        {
            "slide_number": 6,
            "error": {"code": "LLM_TIMEOUT", "message": "LLM 응답 시간이 초과되었습니다."},
        }
    ]


def test_all_slides_failed_is_the_common_error_body(make_client) -> None:
    fakes = _script_fakes()
    fakes["semantic"] = _Raise(LlmTimeout())
    res = make_client(fakes).post("/scripts/rubric", json=_rubric_request())
    assert res.status_code == 504
    assert res.json() == {
        "error": {"code": "LLM_TIMEOUT", "message": "LLM 응답 시간이 초과되었습니다."}
    }


@pytest.mark.parametrize(
    ("path", "body"),
    [("/scripts/rubric", _rubric_request), ("/takes/analyze", _analyze_request)],
)
def test_without_model_settings_llm_routes_are_503(make_client, path, body) -> None:
    res = make_client(settings=Settings(_env_file=None)).post(path, json=body())
    assert res.status_code == 503
    assert res.json()["error"]["code"] == "LLM_UNAVAILABLE"


def test_all_llms_of_a_request_share_one_deadline_of_the_ai_budget(make_client) -> None:
    client = make_client(_script_fakes())
    before = time.monotonic()
    client.post("/scripts/rubric", json=_rubric_request())
    roles = [r for r, _ in client.calls]
    assert roles == ["script_coverage.semantic", "script_coverage.final", "script_coverage.unit"]
    deadlines = {d for _, d in client.calls}
    assert len(deadlines) == 1
    (deadline,) = deadlines
    assert before + api.BUDGET_SECONDS <= deadline <= time.monotonic() + api.BUDGET_SECONDS


def test_bad_request_is_the_common_422(make_client) -> None:
    res = make_client(_script_fakes()).post("/scripts/rubric", json={"pitch_id": "p-1"})
    assert res.status_code == 422
    assert res.json() == {
        "error": {"code": "VALIDATION_ERROR", "message": "요청 형식이 올바르지 않습니다."}
    }


def test_unknown_request_fields_are_ignored(make_client) -> None:
    body = _rubric_request() | {"something_new": 1}
    assert make_client(_script_fakes()).post("/scripts/rubric", json=body).status_code == 200


# ── /takes/analyze ──────────────────────────────────────────────────


def test_analyze_returns_evaluations_take_scores_and_criteria_version(make_client) -> None:
    res = make_client(_take_fakes()).post("/takes/analyze", json=_analyze_request())
    assert res.status_code == 200
    body = res.json()
    assert [e["slide_number"] for e in body["evaluations"]] == list(NUMBERS)
    assert body["take_scores"]["scored_ratio"] == 1.0
    assert body["meta"]["criteria_versions"] == {"script_coverage": core_module.CRITERIA_VERSION}
    assert body["meta"]["model"] == MODEL


def test_slide_without_rubric_is_rubric_missing_and_counts_in_scored_ratio(make_client) -> None:
    res = make_client(_take_fakes()).post("/takes/analyze", json=_analyze_request((4, 6)))
    assert res.status_code == 200
    body = res.json()
    assert [e["slide_number"] for e in body["evaluations"]] == [4, 6]
    assert [(f["slide_number"], f["error"]["code"]) for f in body["failed"]] == [
        (7, "RUBRIC_MISSING")
    ]
    assert body["take_scores"]["scored_ratio"] == round(2 / 3, 3)


def test_no_rubrics_at_all_is_422_rubric_missing(make_client) -> None:
    body = _analyze_request() | {"rubrics": []}
    res = make_client(_take_fakes()).post("/takes/analyze", json=body)
    assert res.status_code == 422
    assert res.json()["error"]["code"] == "RUBRIC_MISSING"


# ── /takes/rescore ──────────────────────────────────────────────────


def test_rescore_example_request_gives_the_example_response(make_client) -> None:
    request = json.loads((EXAMPLES / "rescore" / "request.json").read_text(encoding="utf-8"))
    expected = json.loads((EXAMPLES / "rescore" / "response.json").read_text(encoding="utf-8"))
    # LLM 을 쓰지 않으므로 모델 설정이 없어도 된다
    res = make_client(settings=Settings(_env_file=None)).post("/takes/rescore", json=request)
    assert res.status_code == 200
    assert res.json() == expected


def test_rescore_with_changed_rubric_is_422_rubric_mismatch(make_client) -> None:
    request = json.loads((EXAMPLES / "rescore" / "request.json").read_text(encoding="utf-8"))
    request["rubrics"][0]["meta"]["rubric_id"] = "다른-평가-기준"
    res = make_client(settings=Settings(_env_file=None)).post("/takes/rescore", json=request)
    assert res.status_code == 422
    assert res.json()["error"]["code"] == "RUBRIC_MISMATCH"


# ── 준비 · 로그 ─────────────────────────────────────────────────────


def test_before_the_morpheme_analyzer_is_ready_routes_are_503_not_ready() -> None:
    release = threading.Event()
    app = create_app(FULL)
    app.state.readiness = Readiness([lambda: release.wait(10)])
    with TestClient(app) as client:
        for path in ("/scripts/rubric", "/takes/analyze", "/takes/rescore"):
            res = client.post(path, json={})
            assert res.status_code == 503
            assert res.json()["error"]["code"] == "NOT_READY"
        assert client.get("/ready").status_code == 503
        assert client.get("/health").status_code == 200
        release.set()
        assert app.state.readiness.wait(10)
        assert client.get("/ready").json() == {"status": "ready"}


def test_request_log_has_counts_but_no_script_text(
    make_client, log_records: list[logging.LogRecord]
) -> None:
    make_client(_script_fakes()).post("/scripts/rubric", json=_rubric_request())
    lines = [r.getMessage() for r in log_records if r.name == "pitch_coach_ai.script_coverage"]
    chars = sum(len(_script(n)) for n in NUMBERS)
    assert lines == [
        f"script_coverage op=rubric slides=3 targets=3 chars={chars} failed=0 code=- "
        + lines[0].split("code=- ")[1]
    ]
    assert lines[0].split("code=- ")[1].startswith("duration_ms=")
    assert all(
        _script(n)[:10] not in "\n".join(r.getMessage() for r in log_records) for n in NUMBERS
    )


def test_failed_request_log_has_the_error_code(
    make_client, log_records: list[logging.LogRecord]
) -> None:
    fakes = _script_fakes()
    fakes["semantic"] = _Raise(LlmTimeout())
    make_client(fakes).post("/scripts/rubric", json=_rubric_request())
    (line,) = [r.getMessage() for r in log_records if r.name == "pitch_coach_ai.script_coverage"]
    assert " failed=- code=LLM_TIMEOUT " in line


def test_analyze_one_failed_slide_goes_to_failed_and_lowers_scored_ratio(make_client) -> None:
    fakes = _take_fakes()
    fakes["eval"] = _FailOn(fakes["eval"], "[슬라이드 6]", LlmTimeout())
    res = make_client(fakes).post("/takes/analyze", json=_analyze_request())
    assert res.status_code == 200
    body = res.json()
    assert [e["slide_number"] for e in body["evaluations"]] == [4, 7]
    assert [(f["slide_number"], f["error"]["code"]) for f in body["failed"]] == [(6, "LLM_TIMEOUT")]
    assert body["take_scores"]["scored_ratio"] == round(2 / 3, 3)


def test_analyze_all_slides_failed_is_the_common_error_body(make_client) -> None:
    fakes = _take_fakes()
    fakes["eval"] = _Raise(LlmTimeout())
    res = make_client(fakes).post("/takes/analyze", json=_analyze_request())
    assert res.status_code == 504
    assert res.json() == {
        "error": {"code": "LLM_TIMEOUT", "message": "LLM 응답 시간이 초과되었습니다."}
    }


def test_analyze_eval_and_verifier_share_one_deadline(make_client) -> None:
    client = make_client(_take_fakes())
    client.post("/takes/analyze", json=_analyze_request())
    assert [r for r, _ in client.calls] == ["script_coverage.eval", "script_coverage.verifier"]
    assert len({d for _, d in client.calls}) == 1
