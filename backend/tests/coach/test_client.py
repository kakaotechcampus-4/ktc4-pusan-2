"""코치 AI 호출 — 재시도 · 409 · 응답 모양 · 동시 개수."""

import asyncio
import json
from typing import Any

import httpx
import pytest

from pitch_coach_backend.coach.client import (
    EVALUATE_PATH,
    FINALIZE_PATH,
    PLAN_PATH,
    EvaluateMiss,
)
from pitch_coach_backend.coach.replies import (
    EvaluateReply,
    FinalizeReply,
    PlanReply,
    ReplayRequired,
)
from pitch_coach_backend.core import ai_slots
from tests.coach.conftest import EVALUATE, FINALIZE, PLAN, PLAN_RESPONSE, ai, error, example

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

    assert await ai(handler).evaluate(EVALUATE) is EvaluateMiss.FAILED
    assert seen == [1]


@pytest.mark.anyio
async def test_slow_evaluate_is_dropped():
    async def handler(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(1)
        return httpx.Response(200, json=example("evaluate/response.json"))

    assert await ai(handler, evaluate_timeout_sec=0.05).evaluate(EVALUATE) is EvaluateMiss.TIMEOUT


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
        # coach_events.t_ms 칸(Integer)에 안 들어가는 시각 — 넣으면 뒤 이벤트까지 저장이 막힌다
        lambda body: body["events"][0].update(t_ms=2**31),
    ],
)
async def test_evaluate_reply_that_breaks_the_contract_is_dropped(breaking):
    body = example("evaluate/response.json")
    breaking(body)

    assert (
        await ai(lambda request: httpx.Response(200, json=body)).evaluate(EVALUATE)
        is EvaluateMiss.FAILED
    )


@pytest.mark.anyio
@pytest.mark.parametrize(
    "unstorable",
    [
        lambda body: body["events"][0].update(note="\x00"),
        lambda body: body["meta"].update(latency=float("nan")),
    ],
)
async def test_evaluate_reply_that_cannot_be_stored_is_dropped(unstorable):
    """JSONB 에 넣을 수 없는 값(NUL 문자 · NaN)이 든 이벤트를 받으면 그 저장이 끝내 실패한다."""
    body = example("evaluate/response.json")
    unstorable(body)
    raw = json.dumps(body).encode()

    def reply(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=raw, headers={"content-type": "application/json"})

    assert await ai(reply).evaluate(EVALUATE) is EvaluateMiss.FAILED


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


# ── 동시 개수 ─────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_background_calls_share_one_concurrency_limit(monkeypatch):
    monkeypatch.setattr(ai_slots, "BACKGROUND_LIMIT", 2)
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

    client = ai(handler, evaluate_timeout_sec=1)
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
