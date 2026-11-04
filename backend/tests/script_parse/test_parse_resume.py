"""BE 가 다시 뜰 때 만료 전 PENDING 파싱을 이어받는다 (`ScriptParseRunner.resume_all`)."""

import asyncio
import threading
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from pitch_coach_backend.module.pitch.adapter.script_parser import ScriptParseError
from pitch_coach_backend.module.pitch.dto import ParseSaved, ParseTicket, ScriptParseErrorCode
from pitch_coach_backend.module.pitch.entity import ScriptVersion
from pitch_coach_backend.module.pitch.service import script as script_service
from pitch_coach_backend.module.pitch.service.script_parse_runner import ScriptParseRunner
from tests.script_parse.conftest import (
    SCRIPT_TEXT,
    TARGET_MS,
    TWO_SLIDES,
    FakeParser,
    _age,
    _create_ok,
    _get,
)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _runner(db: Session, parser: FakeParser) -> ScriptParseRunner:
    return ScriptParseRunner(parser, session_factory=lambda: db)


def _status(client: TestClient, pitch_id, script_id, headers) -> tuple[str, str | None]:
    body = _get(client, pitch_id, script_id, headers).json()
    return body["parse_status"], body["error_code"]


@pytest.mark.anyio
async def test_pending_parse_is_resumed_and_finished(
    client: TestClient,
    db_session: Session,
    auth_headers: dict[str, str],
    pitch_id: uuid.UUID,
) -> None:
    """conftest 의 기본 runner 는 아무것도 안 해 PENDING 으로 남는다 — 작업이 사라진 상황."""
    script_id = _create_ok(client, pitch_id, auth_headers)
    parser = FakeParser()

    await _runner(db_session, parser).resume_all()

    assert parser.calls == [SCRIPT_TEXT]
    assert parser.targets == [TARGET_MS]
    assert _status(client, pitch_id, script_id, auth_headers) == ("DONE", None)


@pytest.mark.anyio
async def test_resume_restarts_the_expiry_clock(
    client: TestClient,
    db_session: Session,
    auth_headers: dict[str, str],
    pitch_id: uuid.UUID,
) -> None:
    """남은 시간(30초)이 어댑터 최악 소요(61초)보다 짧아도 AI 가 답하는 중에 만료되지 않게
    표식을 지금으로 바꾼다."""
    script_id = _create_ok(client, pitch_id, auth_headers)
    _age(db_session, script_id, 60)
    script = db_session.get(ScriptVersion, script_id)
    assert script is not None
    before = script.parse_requested_at

    tickets = script_service.resume_parses(db_session)

    db_session.refresh(script)
    assert [ticket.script_version_id for ticket in tickets] == [script_id]
    assert script.parse_requested_at == tickets[0].requested_at
    assert script.parse_requested_at > before


@pytest.mark.anyio
async def test_expired_pending_is_left_for_the_user_to_retry(
    client: TestClient,
    db_session: Session,
    auth_headers: dict[str, str],
    pitch_id: uuid.UUID,
) -> None:
    script_id = _create_ok(client, pitch_id, auth_headers)
    _age(db_session, script_id, 100)
    parser = FakeParser()

    await _runner(db_session, parser).resume_all()

    assert parser.calls == []
    assert _status(client, pitch_id, script_id, auth_headers) == ("FAILED", "PARSE_EXPIRED")


@pytest.mark.anyio
async def test_finished_parses_are_not_resumed(
    client: TestClient,
    db_session: Session,
    parser: FakeParser,
    auth_headers: dict[str, str],
    pitch_id: uuid.UUID,
) -> None:
    """DONE · FAILED 는 이어받지 않는다."""
    done_id = _create_ok(client, pitch_id, auth_headers)
    parser.result = ScriptParseError(ScriptParseErrorCode.AI_TIMEOUT, "slow", retryable=True)
    failed_id = _create_ok(client, pitch_id, auth_headers)
    resumed = FakeParser()

    await _runner(db_session, resumed).resume_all()

    assert resumed.calls == []
    assert _status(client, pitch_id, done_id, auth_headers) == ("DONE", None)
    assert _status(client, pitch_id, failed_id, auth_headers) == ("FAILED", "AI_TIMEOUT")


def test_pending_without_original_text_is_not_resumed(
    client: TestClient,
    db_session: Session,
    auth_headers: dict[str, str],
    pitch_id: uuid.UUID,
) -> None:
    """원문이 DB 에 없는 행(파싱 기능 전 대본)은 다시 보낼 수 없다."""
    script_id = _create_ok(client, pitch_id, auth_headers)
    script = db_session.get(ScriptVersion, script_id)
    assert script is not None
    script.content = None
    db_session.commit()

    assert script_service.resume_parses(db_session) == []


def test_old_job_cannot_overwrite_after_resume(
    client: TestClient,
    db_session: Session,
    auth_headers: dict[str, str],
    pitch_id: uuid.UUID,
) -> None:
    """이어받기 전 작업이 뒤늦게 끝나도 표식이 달라 아무것도 쓰지 못한다."""
    script_id = _create_ok(client, pitch_id, auth_headers)
    script = db_session.get(ScriptVersion, script_id)
    assert script is not None
    old = ParseTicket(script_id, script.parse_requested_at, SCRIPT_TEXT, TARGET_MS)

    [resumed] = script_service.resume_parses(db_session)

    assert script_service.complete_parse(db_session, old, TWO_SLIDES) is ParseSaved.STALE
    assert script_service.complete_parse(db_session, resumed, TWO_SLIDES) is ParseSaved.APPLIED
    assert _status(client, pitch_id, script_id, auth_headers) == ("DONE", None)


@pytest.mark.anyio
async def test_resume_does_not_raise_when_db_is_down(caplog: pytest.LogCaptureFixture) -> None:
    def broken() -> Session:
        raise RuntimeError("DB 연결 실패")

    parser = FakeParser()

    await ScriptParseRunner(parser, session_factory=broken).resume_all()

    assert parser.calls == []
    assert "남은 대본 파싱 작업을 읽지 못했다" in caplog.text


@pytest.mark.anyio
async def test_resume_does_not_take_over_a_new_live_parse(
    client: TestClient,
    db_session: Session,
    auth_headers: dict[str, str],
    pitch_id: uuid.UUID,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """복구 조회가 늦어져도 그 사이 새 요청에서 돌기 시작한 파싱을 다시 부르지 않는다."""
    reading = threading.Event()
    allow_read = threading.Event()
    parsing = asyncio.Event()
    allow_result = asyncio.Event()

    class LiveParser(FakeParser):
        async def parse(self, script_text: str, target_ms: int | None):
            self.calls.append(script_text)
            if len(self.calls) > 1:
                raise ScriptParseError(
                    ScriptParseErrorCode.AI_UNAVAILABLE, "중복 호출 실패", retryable=False
                )
            parsing.set()
            await allow_result.wait()
            return TWO_SLIDES

    parser = LiveParser()
    runner = _runner(db_session, parser)
    resume = runner._resume

    def delayed_resume(*args, **kwargs):
        reading.set()
        assert allow_read.wait(timeout=5)
        return resume(*args, **kwargs)

    monkeypatch.setattr(runner, "_resume", delayed_resume)
    recovery = asyncio.create_task(runner.resume_all())
    live = None
    try:
        assert await asyncio.to_thread(reading.wait, 5)
        script_id = _create_ok(client, pitch_id, auth_headers)
        script = db_session.get(ScriptVersion, script_id)
        assert script is not None
        ticket = ParseTicket(script_id, script.parse_requested_at, SCRIPT_TEXT, TARGET_MS)
        live = asyncio.create_task(runner.run(ticket))
        async with asyncio.timeout(5):
            await parsing.wait()
        allow_read.set()
        async with asyncio.timeout(5):
            await recovery
    finally:
        allow_read.set()
        allow_result.set()
        await asyncio.gather(recovery, *([live] if live is not None else []))

    assert parser.calls == [SCRIPT_TEXT]
    assert _status(client, pitch_id, script_id, auth_headers) == ("DONE", None)
