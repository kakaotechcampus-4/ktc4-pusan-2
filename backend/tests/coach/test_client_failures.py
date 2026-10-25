"""코치 AI 가 없을 때 · 로그."""

import asyncio
import logging
import uuid
from typing import Any

import httpx
import pytest

from pitch_coach_backend.coach import client as coach_client
from pitch_coach_backend.coach.client import EVALUATE_PATH, FINALIZE_PATH, HttpCoachClient
from pitch_coach_backend.coach.replies import MAX_T_MS, PlanReply
from tests.coach.conftest import EVALUATE, FINALIZE, PLAN, PLAN_RESPONSE, Clock, ai, error, example

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
