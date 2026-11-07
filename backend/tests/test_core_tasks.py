"""백그라운드 태스크 부품 (`core/tasks.py`) · 짧은 세션 (`core/database.py`)."""

import asyncio
import logging

import pytest

from pitch_coach_backend.core.database import in_session, with_session
from pitch_coach_backend.core.tasks import (
    TaskRegistry,
    quiet,
    run_periodic,
    start_periodic,
    start_task,
)


class _Session:
    closed = False

    def close(self) -> None:
        self.closed = True


def test_with_session_closes_after_a_failure():
    session = _Session()

    def boom(db: _Session) -> None:
        raise ValueError

    with pytest.raises(ValueError):
        with_session(lambda: session, boom)

    assert session.closed


@pytest.mark.anyio
async def test_in_session_passes_arguments_and_closes():
    session = _Session()

    def step(db: _Session, a: int, *, b: int) -> tuple[_Session, int, int]:
        return db, a, b

    assert await in_session(lambda: session, step, 1, b=2) == (session, 1, 2)
    assert session.closed


@pytest.mark.anyio
async def test_periodic_step_runs_right_away_unless_delayed():
    calls = 0

    async def step() -> None:
        nonlocal calls
        calls += 1

    stop = start_periodic(step, 60.0, name="now")
    await asyncio.sleep(0)
    await stop()
    assert calls == 1

    stop = start_periodic(step, 60.0, name="later", delay_first=True)
    await asyncio.sleep(0)
    await stop()
    assert calls == 1


@pytest.mark.anyio
async def test_stop_survives_a_task_that_already_failed(caplog: pytest.LogCaptureFixture):
    async def step() -> None:
        raise RuntimeError("부서짐")

    stop = start_task(run_periodic(step, 60.0), name="broken")
    await asyncio.sleep(0)

    with caplog.at_level(logging.ERROR):
        await stop()

    assert "broken" in caplog.text


@pytest.mark.anyio
async def test_quiet_swallows_exceptions_but_not_cancellation():
    async def boom() -> None:
        raise ValueError

    await quiet(boom())

    async def cancelled() -> None:
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await quiet(cancelled())


async def _hang() -> None:
    await asyncio.Event().wait()


@pytest.mark.anyio
async def test_registry_cancel_stops_only_that_key():
    registry = TaskRegistry()
    target = registry.spawn(_hang(), name="target", key="target")
    other = registry.spawn(_hang(), name="other", key="other")
    await asyncio.sleep(0)

    assert registry.cancel("target") is True
    assert registry.cancel_requested("target")
    assert not registry.cancel_requested("other")
    await asyncio.wait([target])

    assert target.cancelled()
    assert not other.done()
    assert not registry.running("target")
    await registry.stop_all(1.0)


@pytest.mark.anyio
async def test_registry_cancel_of_nothing_running_is_false():
    registry = TaskRegistry()

    async def done_soon() -> None:
        pass

    task = registry.spawn(done_soon(), name="done", key="done")
    await asyncio.wait([task])

    assert registry.cancel("done") is False
    assert registry.cancel("missing") is False
    assert not registry.cancel_requested("done")
    assert not registry.cancel_requested("missing")


@pytest.mark.anyio
async def test_registry_forgets_the_cancel_before_the_same_key_runs_again():
    registry = TaskRegistry()
    first = registry.spawn(_hang(), name="first", key="same")
    registry.cancel("same")
    await asyncio.wait([first])
    await asyncio.sleep(0)

    second = registry.spawn(_hang(), name="second", key="same")

    assert not registry.cancel_requested("same")
    assert not second.done()
    await registry.stop_all(1.0)
    assert not registry.cancel_requested("same")


@pytest.mark.anyio
async def test_registry_forgets_a_cancel_of_a_task_that_never_started():
    registry = TaskRegistry()
    task = registry.spawn(_hang(), name="never", key="never")

    assert registry.cancel("never") is True
    await asyncio.wait([task])
    await asyncio.sleep(0)

    assert not registry.cancel_requested("never")
    assert len(registry) == 0
