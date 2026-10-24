"""코치 tasks 실행부의 실패·복구 회귀 테스트."""

import asyncio

import pytest

from pitch_coach_backend.core.tasks import TaskRegistry


@pytest.fixture
def anyio_backend():
    return "asyncio"

async def settle() -> None:
    for _ in range(5):
        await asyncio.sleep(0)

@pytest.mark.anyio
async def test_background_task_failure_is_logged_with_its_name(caplog: pytest.LogCaptureFixture):
    async def broken() -> None:
        raise RuntimeError("bug")

    registry = TaskRegistry()
    with caplog.at_level("ERROR"):
        registry.spawn(broken(), name="coach-plan:test")
        await settle()

    assert "백그라운드 태스크 실패 coach-plan:test" in caplog.text
    assert len(registry) == 0
