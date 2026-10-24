"""코치 planner 실행부의 실패·복구 회귀 테스트."""

import asyncio
from datetime import datetime

import pytest

from pitch_coach_backend.coach import planner
from pitch_coach_backend.coach.client import (
    PlanReply,
)
from tests.coach_fakes import (
    PITCH_ID,
    PLAN_REQUEST,
    FakeClient,
    FakeStore,
    example_plan_reply,
    settle,
)


@pytest.fixture
def anyio_backend():
    return "asyncio"

@pytest.mark.anyio
async def test_plan_is_requested_and_saved_with_the_time_it_was_sent():
    client = FakeClient()
    client.plan_reply = PlanReply.model_validate(example_plan_reply())
    store = FakeStore(plan_request=PLAN_REQUEST)

    before = datetime.now().astimezone()
    await planner.run(PITCH_ID, client=client, store=store)

    assert client.planned == [PLAN_REQUEST]
    ((pitch_id, request, reply_, requested_at),) = store.plans
    assert (pitch_id, request) == (PITCH_ID, PLAN_REQUEST)
    assert requested_at >= before

@pytest.mark.anyio
async def test_no_plan_request_or_no_reply_saves_nothing():
    client = FakeClient()
    await planner.run(PITCH_ID, client=client, store=FakeStore(plan_request=None))
    assert client.planned == []

    store = FakeStore(plan_request=PLAN_REQUEST)
    await planner.run(PITCH_ID, client=client, store=store)
    assert store.plans == []

@pytest.mark.anyio
async def test_spawned_plan_runs_in_the_background():
    client = FakeClient()
    client.plan_reply = PlanReply.model_validate(example_plan_reply())
    store = FakeStore(plan_request=PLAN_REQUEST)

    planner.spawn(PITCH_ID, client=client, store=store)
    await asyncio.gather(*planner._running.tasks())

    assert len(store.plans) == 1

@pytest.mark.anyio
async def test_shutdown_stops_running_plan_requests():
    class Slow(FakeClient):
        async def plan(self, request):
            await asyncio.Event().wait()

    store = FakeStore(plan_request=PLAN_REQUEST)
    planner.spawn(PITCH_ID, client=Slow(), store=store)
    await settle()

    await planner.stop_all(1.0)

    assert all(task.done() for task in planner._running.tasks())
    assert store.plans == []
