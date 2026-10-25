"""평가 기준 생성·저장 실패의 로그에는 대본이나 AI 응답 본문이 없어야 한다."""

import asyncio
import logging
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from sqlalchemy.exc import OperationalError

from pitch_coach_backend.module.pitch.adapter.script_rubric import (
    HttpRubricClient,
    RubricCallError,
)
from pitch_coach_backend.module.pitch.dto import RubricTicket
from pitch_coach_backend.module.pitch.service import script_rubric_runner
from pitch_coach_backend.module.pitch.service.script_rubric_runner import ScriptRubricRunner
from tests.test_script_rubric import FakeClient, _queued, _reply, _runner, _state
from tests.test_script_rubric import version_id as version_id

pytestmark = pytest.mark.anyio
PRIVATE = "RUBRIC_PRIVATE_SENTINEL"


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _assert_private(caplog):
    assert PRIVATE not in caplog.text
    assert all(record.exc_info is None for record in caplog.records)


def _ticket():
    return RubricTicket(
        script_version_id=uuid.uuid4(),
        pitch_id=uuid.uuid4(),
        requested_at=datetime.now(UTC),
        slides=((1, PRIVATE),),
        missing=(1,),
        retry_used=False,
    )


@pytest.mark.parametrize(
    "step", ["begin_rubric", "resume_rubrics", "save_rubrics", "finish_rubric"]
)
@pytest.mark.parametrize("state_code", ["08006", PRIVATE])
async def test_db_failure_omits_sql_parameters_and_keeps_pending(
    db_session, version_id, monkeypatch, caplog, step, state_code
):
    started = _queued(db_session, version_id)
    fake = FakeClient(_reply([1, 2, 3]))
    runner = _runner(db_session, fake)

    def fail(*args):
        original = RuntimeError(PRIVATE)
        original.sqlstate = state_code
        raise OperationalError(
            "UPDATE script_slides SET rubric = :rubric", {"rubric": PRIVATE}, original
        )

    monkeypatch.setattr(script_rubric_runner.script_service, step, fail)
    with caplog.at_level(logging.INFO):
        if step == "resume_rubrics":
            await runner.resume_all()
        else:
            await runner.start(version_id, started)
    _assert_private(caplog)
    expected = "08006" if state_code == "08006" else "None"
    assert f"error=OperationalError sqlstate={expected}" in caplog.text
    if step in {"save_rubrics", "finish_rubric"}:
        assert f"request_id={fake.calls[0][1]}" in caplog.text
    assert _state(db_session, version_id)[0] == "PENDING"


@pytest.mark.parametrize(
    ("status", "body", "code"),
    [
        (422, {"error": {"code": "VALIDATION_ERROR", "message": PRIVATE}}, "VALIDATION_ERROR"),
        (503, {"error": {"code": PRIVATE, "message": PRIVATE}}, "UNKNOWN"),
        (500, PRIVATE, "UNKNOWN"),
        (422, [PRIVATE], "UNKNOWN"),
    ],
)
async def test_http_failure_omits_response_body(caplog, status, body, code):
    def respond(request):
        return (
            httpx.Response(status, json=body)
            if not isinstance(body, str)
            else httpx.Response(status, text=body)
        )

    client = HttpRubricClient("http://ai:8000", transport=httpx.MockTransport(respond))
    runner = ScriptRubricRunner(client, session_factory=MagicMock)
    ticket = _ticket()
    with caplog.at_level(logging.WARNING), pytest.raises(RubricCallError) as caught:
        await runner._call(ticket, None, "req-privacy")
    _assert_private(caplog)
    assert PRIVATE not in str(caught.value)
    assert caught.value.retryable is (status >= 500)
    assert f"status={status} ai_error_code={code}" in caplog.text
    assert "request_id=req-privacy" in caplog.text


async def test_transport_failure_omits_exception_message(caplog):
    def respond(request):
        raise httpx.ConnectError(PRIVATE)

    runner = ScriptRubricRunner(
        HttpRubricClient("http://ai:8000", transport=httpx.MockTransport(respond))
    )
    with caplog.at_level(logging.WARNING), pytest.raises(RubricCallError) as caught:
        await runner._call(_ticket(), None, "req-transport")
    _assert_private(caplog)
    assert PRIVATE not in str(caught.value)
    assert caught.value.retryable


async def test_partial_failure_code_is_sanitized_only_in_the_log(caplog):
    reply = _reply([], {1: PRIVATE, 2: "LLM_TIMEOUT", 3: "RUBRIC_MISSING"})
    client = AsyncMock()
    client.generate.return_value = reply
    runner = ScriptRubricRunner(client, session_factory=MagicMock)
    with caplog.at_level(logging.INFO):
        returned = await runner._call(_ticket(), None, "req-partial")
    _assert_private(caplog)
    assert returned.failed == reply.failed
    assert "UNKNOWN" in caplog.text
    assert "LLM_TIMEOUT" in caplog.text
    assert "RUBRIC_MISSING" in caplog.text


async def test_concurrent_unexpected_failures_keep_their_own_request_id(caplog):
    requests = {}

    class BrokenClient:
        async def generate(self, **kwargs):
            requests[kwargs["script_version_id"]] = kwargs["request_id"]
            await asyncio.sleep(0)
            raise RuntimeError(PRIVATE)

    runner = ScriptRubricRunner(BrokenClient(), session_factory=MagicMock)
    tickets = [_ticket(), _ticket()]
    with caplog.at_level(logging.ERROR):
        await asyncio.gather(*(runner._run(ticket) for ticket in tickets))
    _assert_private(caplog)
    records = [record.getMessage() for record in caplog.records]
    for ticket in tickets:
        [message] = [text for text in records if str(ticket.script_version_id) in text]
        assert f"request_id={requests[str(ticket.script_version_id)]}" in message
        assert "error=RuntimeError sqlstate=None" in message
