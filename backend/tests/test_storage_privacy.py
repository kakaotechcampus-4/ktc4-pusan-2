"""API·코치 저장 오류는 원문 대신 안전한 진단값만 기록한다."""

import asyncio
import logging
import socket
import sys
import threading
import time
import uuid
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
import uvicorn
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.exc import DataError, OperationalError

from pitch_coach_backend.coach import finalize, planner
from pitch_coach_backend.core.exceptions import register_exception_handlers
from pitch_coach_backend.core.logging import configure_logging
from pitch_coach_backend.core.retry import retry_transient
from pitch_coach_backend.core.row_buffer import RowBuffer
from pitch_coach_backend.core.security import create_access_token
from pitch_coach_backend.module.pitch.repository import PitchRepository
from tests.coach_fakes import snapshot
from tests.test_take_history import _pitch

PRIVATE = "STORAGE_PRIVACY_SYNTHETIC_MARKER"


@pytest.fixture(autouse=True)
def isolated_server_logging():
    logger = logging.getLogger("uvicorn.error")
    filters = list(logger.filters)
    logger.filters.clear()
    yield
    logger.filters[:] = filters


def db_error(kind=OperationalError):
    original = RuntimeError(PRIVATE)
    original.sqlstate = "22000" if kind is DataError else "08006"
    return kind("INSERT synthetic", {"payload": PRIVATE}, original)


def assert_private(caplog):
    assert PRIVATE not in caplog.text
    assert all(record.exc_info is None for record in caplog.records)
    assert "sqlstate=" in caplog.text


def test_http_storage_failure_keeps_safe_500(client, db_session, user_id, monkeypatch, caplog):
    pitch = _pitch(db_session, user_id)

    def fail(*args):
        raise db_error()

    monkeypatch.setattr(PitchRepository, "save_script", fail)
    with (
        TestClient(
            client.app, base_url="https://testserver", raise_server_exceptions=False
        ) as safe,
        caplog.at_level(logging.ERROR),
    ):
        response = safe.post(
            f"/api/pitches/{pitch.id}/scripts",
            json={"content": PRIVATE},
            headers={"Authorization": f"Bearer {create_access_token(user_id)}"},
        )
    assert response.status_code == 500
    assert response.json() == {
        "code": "INTERNAL_SERVER_ERROR",
        "message": "서버 오류가 발생했습니다.",
        "detail": None,
    }
    assert_private(caplog)
    assert f"POST /api/pitches/{pitch.id}/scripts" in caplog.text
    assert "error=OperationalError sqlstate=08006" in caplog.text


@pytest.mark.parametrize("recovers", [True, False])
def test_retry_omits_original_error_and_preserves_attempts(recovers, caplog):
    calls = 0
    original = db_error()

    async def run():
        async def step():
            nonlocal calls
            calls += 1
            if recovers and calls == 2:
                return 42
            raise original

        if recovers:
            assert await retry_transient(step, [0], what="save synthetic") == 42
        else:
            with pytest.raises(OperationalError) as caught:
                await retry_transient(step, [0], what="save synthetic")
            assert caught.value is original

    with caplog.at_level(logging.WARNING):
        asyncio.run(run())
    assert calls == 2
    assert_private(caplog)
    assert "save synthetic" in caplog.text
    assert "error=OperationalError sqlstate=08006" in caplog.text


@pytest.mark.parametrize("kind", [OperationalError, DataError])
def test_row_buffer_preserves_transient_rows_and_drops_permanent_rows(kind, caplog):
    async def run():
        async def fail(*args):
            raise db_error(kind)

        buffer = RowBuffer(
            uuid.uuid4(),
            fail,
            what="coach events",
            batch=100,
            key=lambda row: 1000,
            max_unsaved=100,
        )
        buffer.add({"text": PRIVATE})
        try:
            assert await buffer.flush() is False
            assert len(buffer) == (1 if kind is OperationalError else 0)
        finally:
            buffer.close()

    with caplog.at_level(logging.ERROR):
        asyncio.run(run())
    assert_private(caplog)
    code = "08006" if kind is OperationalError else "22000"
    assert f"error={kind.__name__} sqlstate={code}" in caplog.text


@pytest.mark.parametrize(
    "kind,mark_fails", [(OperationalError, False), (DataError, False), (DataError, True)]
)
def test_finalize_failure_preserves_pending_and_failure_policy(
    kind, mark_fails, monkeypatch, caplog
):
    async def fail(*args):
        raise db_error(kind)

    pending = {}
    monkeypatch.setattr(finalize, "_run", fail)
    monkeypatch.setattr(finalize, "_pending", pending)
    monkeypatch.setattr(finalize, "_slots_by_loop", {})
    monkeypatch.setattr(finalize, "RETRY_DELAYS_SEC", ())
    store = MagicMock()
    store.fail_take_result = AsyncMock(side_effect=db_error() if mark_fails else None)
    take_id = uuid.uuid4()
    with caplog.at_level(logging.ERROR):
        asyncio.run(finalize.run(take_id, snapshot(), client=MagicMock(), store=store))
    assert_private(caplog)
    assert str(take_id) in caplog.text
    assert store.fail_take_result.await_count == (0 if kind is OperationalError else 1)
    assert (take_id in pending) is (kind is OperationalError or mark_fails)


def test_finalize_pending_events_failure_omits_original(caplog):
    store = MagicMock()
    store.append_events = AsyncMock(side_effect=db_error())
    with caplog.at_level(logging.ERROR):
        asyncio.run(finalize._keep_events(uuid.uuid4(), (), store))
    assert_private(caplog)


def test_finalize_recovery_failure_omits_original(monkeypatch, caplog):
    monkeypatch.setattr(finalize, "_pending", {})
    store = MagicMock()
    store.finalizing_takes = AsyncMock(side_effect=db_error())
    with caplog.at_level(logging.ERROR):
        asyncio.run(finalize._resume(MagicMock(), store, None))
    assert_private(caplog)


@pytest.mark.parametrize("phase", ["build", "call", "save"])
def test_plan_failure_omits_original_and_keeps_call_order(phase, caplog):
    store = MagicMock()
    store.build_plan_request = AsyncMock(return_value={"pitch_id": "synthetic"})
    store.save_plan = AsyncMock(return_value=True)
    client = MagicMock()
    client.plan = AsyncMock(return_value=MagicMock())
    failed = {"build": store.build_plan_request, "call": client.plan, "save": store.save_plan}[
        phase
    ]
    failed.side_effect = RuntimeError(PRIVATE) if phase == "call" else db_error()
    with caplog.at_level(logging.ERROR):
        asyncio.run(planner.run(uuid.uuid4(), client=client, store=store))
    assert_private(caplog)
    assert client.plan.await_count == (0 if phase == "build" else 1)
    assert store.save_plan.await_count == (1 if phase == "save" else 0)


def test_real_uvicorn_does_not_relog_the_original_error(caplog):
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/private")
    def fail():
        raise db_error()

    configure_logging("INFO")
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            log_config=None,
            lifespan="off",
            loop="asyncio",
            http="h11",
            ws="none",
            access_log=False,
        )
    )
    worker = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
    with caplog.at_level(logging.ERROR):
        try:
            worker.start()
            deadline = time.monotonic() + 5
            while not server.started:
                assert worker.is_alive() and time.monotonic() < deadline
                time.sleep(0.01)
            response = httpx.get(f"http://127.0.0.1:{port}/private", trust_env=False, timeout=5)
            assert response.status_code == 500
        finally:
            server.should_exit = True
            worker.join(timeout=5)
            listener.close()
    assert not worker.is_alive()
    assert_private(caplog)
    server_records = [record for record in caplog.records if record.name == "uvicorn.error"]
    assert server_records
    assert "error=OperationalError sqlstate=08006" in server_records[0].getMessage()


@pytest.mark.parametrize("cached", [False, True])
def test_server_filter_removes_cached_tracebacks(cached, caplog):
    logger = logging.getLogger("uvicorn.error")
    configure_logging("INFO")
    try:
        raise db_error()
    except OperationalError:
        record = logger.makeRecord(
            logger.name,
            logging.ERROR,
            __file__,
            1,
            "Exception in ASGI application",
            (),
            sys.exc_info(),
        )
    if cached:
        logging.Formatter().format(record)
        assert PRIVATE in record.exc_text
    with caplog.at_level(logging.ERROR):
        logger.handle(record)
    assert_private(caplog)
    assert record.exc_text is None


def test_server_filter_is_installed_once():
    configure_logging("INFO")
    logger = logging.getLogger("uvicorn.error")
    first = list(logger.filters)
    configure_logging("INFO")
    assert first
    assert logger.filters == first
