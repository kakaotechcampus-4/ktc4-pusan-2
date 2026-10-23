"""코치 AI 클라이언트 — 응답 읽기 · 재시도 · 409 · 동시 개수 · AI 가 없을 때.

AI 서버의 `/coach/*` 는 아직 없다. 응답은 AI 연구 폴더의 예시 파일(`ai/research/coach-agent/src/
coach/examples/`)을 그대로 읽어 계약과 같은 모양인지 본다. 예시는 AI 가 생성 스크립트로 만드는
파일이라 계약이 바뀌면 같이 바뀐다 — 다만 backend CI 는 `backend/**` 가 바뀔 때만 돌아서, AI 가
예시만 바꾼 PR 에서는 이 테스트가 다시 돌지 않는다.
"""

import asyncio
import copy
import json
import logging
import uuid
from pathlib import Path
from typing import Any

import httpx
import pytest

from pitch_coach_backend.coach import client as coach_client
from pitch_coach_backend.coach.client import (
    EVALUATE_PATH,
    FINALIZE_PATH,
    MAX_T_MS,
    PLAN_PATH,
    EvaluateReply,
    FinalizeReply,
    HttpCoachClient,
    PlanReply,
    ReplayRequired,
    SummaryMeasures,
    summary_measures,
)

EXAMPLES = Path(__file__).resolve().parents[2] / "ai/research/coach-agent/src/coach/examples"


def example(name: str) -> dict[str, Any]:
    return json.loads((EXAMPLES / name).read_text(encoding="utf-8"))


# plan 은 AI 예시 파일이 없다. coach-agent.md 5-5 의 예시를 옮겼다
PLAN_RESPONSE: dict[str, Any] = {
    "coaching_plan": {
        "focus": [
            {"area": "GAZE", "slide_number": 3, "weight": 1.5, "why": "직전 Take 에서 대본 응시"}
        ],
        "relax": [{"area": "FILLER", "slide_number": 1, "why": "인사 장"}],
        "max_interventions": None,
    },
    "source_take_id": "take-122",
    "script_version_id": "sv-3",
    "fallback_reason": None,
    "dropped": [],
    "planner_hash": "0123456789ab",
    "meta": {"schema_version": "1.0", "feature_version": "coach-1.2", "model": "openai/gpt"},
}


@pytest.fixture
def anyio_backend():
    return "asyncio"


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def ai(handler, **kwargs: Any) -> HttpCoachClient:
    kwargs.setdefault("retry_delay_sec", 0)
    return HttpCoachClient("http://ai.test", transport=httpx.MockTransport(handler), **kwargs)


def error(status: int, code: str, message: str = "m") -> httpx.Response:
    return httpx.Response(status, json={"error": {"code": code, "message": message}})


EVALUATE = {"take_id": "take-1", "t_ms": 92000}
FINALIZE = {"take_id": "take-1", "t_ms": 92000}
PLAN = {"pitch_id": "p-1"}


# ── 응답 읽기 ─────────────────────────────────────────────────────────


def test_evaluate_example_reads_and_keeps_state_and_events_untouched():
    body = example("evaluate/response.json")

    reply = EvaluateReply.model_validate(body)

    assert reply.intervened
    assert reply.feedback is not None and reply.feedback.area == "GAZE"
    assert reply.feedback.evidence == body["feedback"]["evidence"]
    assert reply.indicators == body["indicators"]
    # AI 로 다시 가는 값과 저장할 값은 받은 그대로다
    assert reply.coach_state == body["coach_state"]
    assert reply.events == body["events"]
    assert reply.meta == body["meta"]


@pytest.mark.parametrize("name", ["finalize/response.json", "finalize/response_replay.json"])
def test_finalize_example_keeps_the_whole_take_result(name: str):
    body = example(name)

    reply = FinalizeReply.model_validate(body)

    assert reply.take_result == body["take_result"]
    assert reply.events == body["events"]
    assert reply.duration_sec == 92


def test_fields_the_ai_adds_survive_until_storage():
    body = example("finalize/response.json")
    body["take_result"]["new_field"] = {"x": 1}
    body["events"][0]["new_field"] = 1
    body["meta"]["new_field"] = "y"
    body["new_top_level"] = True

    reply = FinalizeReply.model_validate(body)

    assert reply.take_result["new_field"] == {"x": 1}
    assert reply.events[0]["new_field"] == 1
    assert reply.meta["new_field"] == "y"


def test_plan_reply_keeps_why_for_storage():
    reply = PlanReply.model_validate(PLAN_RESPONSE)

    assert reply.coaching_plan == PLAN_RESPONSE["coaching_plan"]
    assert (reply.source_take_id, reply.script_version_id) == ("take-122", "sv-3")


def test_summary_measures_from_the_example():
    measures = summary_measures(example("finalize/response.json")["take_result"])

    assert measures == SummaryMeasures(
        average_wpm=297.0,
        filler_count=2,
        audience_gaze=78,
        slide_gaze=0,
        script_gaze=22,
        script_dependency_count=1,
        volume=0.0,
        total_intermission=0,
        # GAZE 0.9674 · 나머지 넷 1.0 의 평균. TIME 은 넣지 않는다
        overall_confidence=99.35,
    )


def test_summary_leaves_unmeasured_values_empty():
    take_result = copy.deepcopy(example("finalize/response.json")["take_result"])
    take_result["areas"]["GAZE"].update(
        {"measured_ratio": 0.1, "take": None, "unmeasured_reason": "LOW_MEASURED_RATIO"}
    )
    take_result["areas"]["SPEED"]["take"]["cpm"] = None
    del take_result["areas"]["FILLER"]

    measures = summary_measures(take_result)

    assert (measures.audience_gaze, measures.script_dependency_count) == (None, None)
    assert measures.average_wpm is None
    assert measures.filler_count is None
    # 측정 비율은 낮아도 있으면 평균에 들어간다 (0.1 · 1.0 · 1.0 · 1.0)
    assert measures.overall_confidence == 77.5


@pytest.mark.parametrize(
    ("ratio", "percent"), [(0.125, 13), (0.285, 29), (0.145, 15), (0.565, 57), (0.0, 0), (1, 100)]
)
def test_summary_rounds_half_up(ratio: float, percent: int):
    # 0.285 * 100 은 이진 소수로 28.499… 라 floor(x + 0.5) 로는 28 이 된다
    take_result = copy.deepcopy(example("finalize/response.json")["take_result"])
    take_result["areas"]["GAZE"]["take"]["audience_ratio"] = ratio
    take_result["areas"]["GAZE"]["take"]["reading_runs"] = 2.5

    measures = summary_measures(take_result)

    assert (measures.audience_gaze, measures.script_dependency_count) == (percent, 3)


# ── 호출 ──────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_calls_unversioned_paths_with_one_request_id_each():
    seen: list[tuple[str, str, dict[str, Any]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(
            (request.url.path, request.headers["X-Request-ID"], json.loads(request.content))
        )
        if request.url.path == EVALUATE_PATH:
            return httpx.Response(200, json=example("evaluate/response.json"))
        if request.url.path == FINALIZE_PATH:
            return httpx.Response(200, json=example("finalize/response.json"))
        return httpx.Response(200, json=PLAN_RESPONSE)

    client = ai(handler)
    evaluated = await client.evaluate(EVALUATE)
    finalized = await client.finalize(FINALIZE)
    planned = await client.plan(PLAN)

    assert [path for path, _, _ in seen] == [EVALUATE_PATH, FINALIZE_PATH, PLAN_PATH]
    assert [body for _, _, body in seen] == [EVALUATE, FINALIZE, PLAN]
    ids = [request_id for _, request_id, _ in seen]
    assert len(set(ids)) == 3 and all(len(i) == 32 for i in ids)
    assert isinstance(evaluated, EvaluateReply)
    assert isinstance(finalized, FinalizeReply)
    assert isinstance(planned, PlanReply)


@pytest.mark.anyio
@pytest.mark.parametrize("call", ["finalize", "plan"])
async def test_background_retry_keeps_the_request_id(call: str):
    ids: list[str] = []
    ok = example("finalize/response.json") if call == "finalize" else PLAN_RESPONSE

    def handler(request: httpx.Request) -> httpx.Response:
        ids.append(request.headers["X-Request-ID"])
        return error(503, "NOT_READY") if len(ids) == 1 else httpx.Response(200, json=ok)

    reply = await getattr(ai(handler), call)(FINALIZE if call == "finalize" else PLAN)

    assert reply is not None
    assert len(ids) == 2 and ids[0] == ids[1]


@pytest.mark.anyio
@pytest.mark.parametrize("call", ["finalize", "plan"])
@pytest.mark.parametrize(
    ("status", "calls"),
    [(500, 2), (502, 2), (503, 2), (504, 2), (408, 2), (429, 2), (400, 1), (404, 1), (422, 1)],
)
async def test_background_calls_retry_only_failures_that_may_pass(
    call: str, status: int, calls: int
):
    seen: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(status)
        return error(status, "X")

    assert await getattr(ai(handler), call)(FINALIZE if call == "finalize" else PLAN) is None
    assert len(seen) == calls


@pytest.mark.anyio
async def test_background_calls_retry_a_lost_connection_and_a_timeout_once():
    seen: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        if request.url.path == FINALIZE_PATH:
            raise httpx.ConnectError("refused", request=request)
        await asyncio.sleep(1)
        return httpx.Response(200, json=PLAN_RESPONSE)

    client = ai(handler, plan_timeout_sec=0.05)

    assert await client.finalize(FINALIZE) is None
    assert await client.plan(PLAN) is None
    assert seen == [FINALIZE_PATH, FINALIZE_PATH, PLAN_PATH, PLAN_PATH]


@pytest.mark.anyio
@pytest.mark.parametrize(
    "response",
    [
        error(500, "INTERNAL_ERROR"),
        error(503, "NOT_READY"),
        error(422, "VALIDATION_ERROR"),
        httpx.Response(200, json={"action": "WAIT"}),
    ],
)
async def test_evaluate_is_never_retried(response: httpx.Response):
    seen: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(1)
        return response

    assert await ai(handler).evaluate(EVALUATE) is None
    assert seen == [1]


@pytest.mark.anyio
async def test_slow_evaluate_is_dropped():
    async def handler(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(1)
        return httpx.Response(200, json=example("evaluate/response.json"))

    assert await ai(handler, evaluate_timeout_sec=0.05).evaluate(EVALUATE) is None


@pytest.mark.anyio
async def test_replay_call_gets_the_longer_time_limit():
    async def handler(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(0.2)
        return httpx.Response(200, json=example("finalize/response_replay.json"))

    client = ai(handler, finalize_timeout_sec=0.05, replay_timeout_sec=2, attempts=1)

    assert await client.finalize(FINALIZE) is None
    replayed = await client.finalize({**FINALIZE, "replay": {"words": []}})
    assert isinstance(replayed, FinalizeReply)
    assert replayed.take_result["replayed"] is True


# ── 409 REPLAY_REQUIRED ───────────────────────────────────────────────


@pytest.mark.anyio
async def test_replay_required_is_a_result_not_a_failure():
    seen: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(1)
        return error(409, "REPLAY_REQUIRED", "빠진 구간 [60000, 100000)")

    assert await ai(handler).finalize(FINALIZE) == ReplayRequired()
    # 다시 부르지 않는다 — replay 를 만들어 붙이는 건 호출자다
    assert seen == [1]


@pytest.mark.anyio
async def test_replay_required_again_with_replay_is_a_failure():
    client = ai(lambda request: error(409, "REPLAY_REQUIRED"))

    assert await client.finalize({**FINALIZE, "replay": {"words": []}}) is None


@pytest.mark.anyio
async def test_other_conflicts_are_failures():
    assert await ai(lambda request: error(409, "CONFLICT")).finalize(FINALIZE) is None


# ── 응답 모양 ─────────────────────────────────────────────────────────


@pytest.mark.anyio
@pytest.mark.parametrize(
    "breaking",
    [
        lambda body: body.pop("coach_state"),
        lambda body: body.pop("meta"),
        lambda body: body["events"][0].pop("event_id"),
        lambda body: body["meta"].update(schema_version="2.0"),
    ],
)
async def test_evaluate_reply_that_breaks_the_contract_is_dropped(breaking):
    body = example("evaluate/response.json")
    breaking(body)

    assert await ai(lambda request: httpx.Response(200, json=body)).evaluate(EVALUATE) is None


@pytest.mark.anyio
@pytest.mark.parametrize(
    "breaking",
    [
        lambda body: body.pop("take_result"),
        lambda body: body["take_result"].pop("duration_ms"),
        lambda body: body["take_result"]["areas"].update(GAZE=[]),
    ],
)
async def test_finalize_reply_that_breaks_the_contract_is_a_failure(breaking):
    body = example("finalize/response.json")
    breaking(body)
    seen: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(1)
        return httpx.Response(200, json=body)

    assert await ai(handler).finalize(FINALIZE) is None
    # 다시 불러도 같은 모양이 온다
    assert seen == [1]


@pytest.mark.anyio
async def test_plan_fallback_is_a_normal_reply():
    body = {
        **PLAN_RESPONSE,
        "coaching_plan": {"focus": [], "relax": [], "max_interventions": None},
        "fallback_reason": "LLM_TIMEOUT",
        "meta": {**PLAN_RESPONSE["meta"], "model": None},
    }

    reply = await ai(lambda request: httpx.Response(200, json=body)).plan(PLAN)

    assert reply is not None and reply.fallback_reason == "LLM_TIMEOUT"


# ── AI 가 없을 때 ─────────────────────────────────────────────────────


@pytest.mark.anyio
@pytest.mark.parametrize("down", ["refused", 404, 503])
async def test_evaluate_rests_while_the_ai_is_down(down):
    clock = Clock()
    seen: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(1)
        if down == "refused":
            raise httpx.ConnectError("refused", request=request)
        return error(down, "NOT_FOUND" if down == 404 else "NOT_READY")

    client = ai(handler, clock=clock, evaluate_pause_sec=10)

    assert await client.evaluate(EVALUATE) is None
    clock.now += 9.9
    # 쉬는 동안은 부르지 않는다 (다른 Take 도 같은 클라이언트를 쓴다)
    assert await client.evaluate({**EVALUATE, "take_id": "take-2"}) is None
    assert seen == [1]
    clock.now += 0.2
    assert await client.evaluate(EVALUATE) is None
    assert seen == [1, 1]


@pytest.mark.anyio
async def test_evaluate_does_not_rest_after_a_slow_or_broken_reply():
    clock = Clock()
    seen: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(1)
        return error(500, "INTERNAL_ERROR")

    client = ai(handler, clock=clock)
    await client.evaluate(EVALUATE)
    await client.evaluate(EVALUATE)

    assert seen == [1, 1]


@pytest.mark.anyio
async def test_a_dropped_connection_is_retried_but_does_not_pause_evaluate():
    """연결은 됐는데 주고받다 끊긴 것은 그 요청 하나의 일이다. AI 가 없다고 보지 않는다."""
    clock = Clock()
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        raise httpx.ReadError("reset", request=request)

    client = ai(handler, clock=clock)

    assert await client.evaluate(EVALUATE) is None
    assert await client.evaluate(EVALUATE) is None
    assert await client.finalize(FINALIZE) is None
    assert seen == [EVALUATE_PATH, EVALUATE_PATH, FINALIZE_PATH, FINALIZE_PATH]


@pytest.mark.anyio
@pytest.mark.parametrize("base_url", ["ai:8000", "http://[::1"])
async def test_a_wrong_ai_address_is_not_retried_and_does_not_raise(base_url: str):
    client = HttpCoachClient(base_url, retry_delay_sec=0)

    assert await client.finalize(FINALIZE) is None
    assert await client.plan(PLAN) is None
    assert await client.evaluate(EVALUATE) is None


@pytest.mark.anyio
@pytest.mark.parametrize(
    "request_body",
    [
        {**EVALUATE, "take_id": uuid.uuid7()},
        {**EVALUATE, "inputs": {"voice_records": [{"t_ms": 0, "level_db": float("nan")}]}},
    ],
)
async def test_a_request_that_is_not_json_is_a_failure_not_an_exception(
    request_body: dict[str, Any],
):
    seen: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(1)
        return httpx.Response(200, json=example("evaluate/response.json"))

    client = ai(handler)

    assert await client.evaluate(request_body) is None
    assert await client.finalize(request_body) is None
    assert seen == []


@pytest.mark.anyio
async def test_out_of_range_t_ms_is_not_sent():
    seen: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(1)
        return httpx.Response(200, json=example("evaluate/response.json"))

    client = ai(handler)

    assert await client.evaluate({**EVALUATE, "t_ms": MAX_T_MS + 1}) is None
    assert await client.finalize({**FINALIZE, "t_ms": -1}) is None
    assert await client.evaluate({**EVALUATE, "t_ms": MAX_T_MS}) is not None
    assert seen == [1]


# ── 동시 개수 ─────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_background_calls_share_one_concurrency_limit():
    running = 0
    peak = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        await asyncio.sleep(0.02)
        running -= 1
        if request.url.path == PLAN_PATH:
            return httpx.Response(200, json=PLAN_RESPONSE)
        if request.url.path == EVALUATE_PATH:
            return httpx.Response(200, json=example("evaluate/response.json"))
        return httpx.Response(200, json=example("finalize/response.json"))

    client = ai(handler, background_concurrency=2, evaluate_timeout_sec=1)
    results = await asyncio.gather(
        *(client.finalize(FINALIZE) for _ in range(3)), *(client.plan(PLAN) for _ in range(3))
    )

    assert all(r is not None for r in results)
    assert peak == 2

    # evaluate 는 제한 밖이다. 백그라운드 호출이 자리를 다 써도 기다리지 않는다
    peak = 0
    results = await asyncio.gather(
        *(client.finalize(FINALIZE) for _ in range(2)), client.evaluate(EVALUATE)
    )
    assert all(r is not None for r in results)
    assert peak == 3


# ── 로그 ──────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_logs_have_the_error_code_but_not_the_body(caplog: pytest.LogCaptureFixture):
    secret = "발표 대본 원문"
    client = ai(lambda request: error(422, "VALIDATION_ERROR", secret))

    with caplog.at_level(logging.WARNING):
        assert await client.finalize(FINALIZE) is None

    assert "status=422 code=VALIDATION_ERROR" in caplog.text
    assert secret not in caplog.text


@pytest.mark.anyio
async def test_shape_mismatch_log_has_the_place_but_not_the_value(
    caplog: pytest.LogCaptureFixture,
):
    secret = "청중에게 전사된 말"
    body = example("finalize/response.json")
    body["take_result"]["duration_ms"] = secret

    with caplog.at_level(logging.WARNING):
        assert await ai(lambda request: httpx.Response(200, json=body)).finalize(FINALIZE) is None

    assert "take_result" in caplog.text
    assert secret not in caplog.text


@pytest.mark.anyio
async def test_repeated_evaluate_warnings_are_folded(caplog: pytest.LogCaptureFixture):
    clock = Clock()
    client = ai(lambda request: error(422, "VALIDATION_ERROR"), clock=clock)

    with caplog.at_level(logging.WARNING):
        for _ in range(5):
            await client.evaluate(EVALUATE)
        clock.now += coach_client.EVALUATE_WARN_INTERVAL_SEC
        await client.evaluate(EVALUATE)

    messages = [r.getMessage() for r in caplog.records if r.name == coach_client.__name__]
    assert len(messages) == 2
    assert "4번 생략" in messages[1]


@pytest.mark.anyio
async def test_slow_evaluate_is_warned_once_a_minute(caplog: pytest.LogCaptureFixture):
    """시간 초과가 이어지면 그 사이 코칭이 멈춘 것이다. 묶어서라도 경고로 남긴다."""
    clock = Clock()
    client = ai(lambda request: error(504, "TIMEOUT"), clock=clock)

    with caplog.at_level(logging.WARNING):
        for _ in range(3):
            await client.evaluate(EVALUATE)

    messages = [r.getMessage() for r in caplog.records if r.name == coach_client.__name__]
    assert len(messages) == 1 and "status=504" in messages[0]


def test_background_limit_works_on_a_second_event_loop():
    """asyncio.Semaphore 는 처음 기다린 루프에 묶인다. 루프가 바뀌어도(TestClient) 써야 한다."""

    async def handler(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(0.01)
        return httpx.Response(200, json=PLAN_RESPONSE)

    client = ai(handler, background_concurrency=1)

    async def contend() -> list[PlanReply | None]:
        return await asyncio.gather(client.plan(PLAN), client.plan(PLAN))

    assert all(asyncio.run(contend()))
    assert all(asyncio.run(contend()))


@pytest.mark.anyio
async def test_large_reply_is_logged(caplog: pytest.LogCaptureFixture, monkeypatch):
    monkeypatch.setitem(coach_client._LARGE_BODY_BYTES, EVALUATE_PATH, 1_000)
    body = example("evaluate/response.json")

    with caplog.at_level(logging.WARNING):
        reply = await ai(lambda request: httpx.Response(200, json=body)).evaluate(EVALUATE)

    assert reply is not None
    assert "코치 응답이 크다 path=/coach/evaluate take=take-1" in caplog.text
