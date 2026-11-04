"""파싱 작업(runner): 저장이 실패해도 던지지 않고 PENDING 으로 두지 않는다."""

import logging
import uuid
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from pitch_coach_backend.core import ai_slots
from pitch_coach_backend.module.pitch.adapter.script_parser import (
    ParsedScript,
    ScriptParseError,
)
from pitch_coach_backend.module.pitch.dto import ParseSaved, ParseTicket, ScriptParseErrorCode
from pitch_coach_backend.module.pitch.service import script as script_service
from pitch_coach_backend.module.pitch.service import script_parse_runner as parse_runner
from pitch_coach_backend.module.pitch.service.script_parse_runner import ScriptParseRunner
from tests.script_parse.conftest import (
    SCRIPT_TEXT,
    TARGET_MS,
    TWO_SLIDES,
    FakeParser,
    _create_ok,
    _get,
    _reparse,
)

# ── runner: 저장이 실패해도 던지지 않는다 ─────────────────────────────


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _broken_session() -> Session:
    raise RuntimeError("DB 연결 실패")


@pytest.mark.anyio
@pytest.mark.parametrize(
    "result",
    [
        pytest.param(TWO_SLIDES, id="result-save-fails"),
        pytest.param(
            ScriptParseError(ScriptParseErrorCode.AI_TIMEOUT, "slow", retryable=True),
            id="failure-save-fails",
        ),
    ],
)
async def test_runner_swallows_db_failures_and_logs(
    caplog: pytest.LogCaptureFixture, result: ParsedScript | Exception
) -> None:
    """응답은 이미 나갔으니 던질 곳이 없다. 로그만 남긴다 (행은 PENDING 으로 남아 만료를 기다린다).

    DB 가 없는 테스트라 행 상태가 아니라 "던지지 않는다"와 로그만 본다.
    """
    runner = ScriptParseRunner(FakeParser(result), session_factory=_broken_session)
    ticket = ParseTicket(uuid.uuid7(), datetime.now(UTC), SCRIPT_TEXT, TARGET_MS)

    with caplog.at_level(logging.ERROR):
        await runner.run(ticket)  # 예외가 나오면 테스트 실패

    assert any("저장 실패" in record.message for record in caplog.records)


def test_save_failure_ends_as_failed_not_pending(
    client: TestClient,
    parser: FakeParser,
    monkeypatch: pytest.MonkeyPatch,
    auth_headers: dict[str, str],
    pitch_id: uuid.UUID,
) -> None:
    """결과 저장이 값 문제로 실패하면 FAILED(INTERNAL_ERROR). PENDING 으로 두면 FE 가 90초를
    기다린 뒤 엉뚱한 PARSE_EXPIRED 를 받는다."""

    def broken_complete(*args: object) -> ParseSaved:
        raise ValueError("저장할 수 없는 값")

    monkeypatch.setattr(script_service, "complete_parse", broken_complete)
    script_id = _create_ok(client, pitch_id, auth_headers)

    body = _get(client, pitch_id, script_id, auth_headers).json()

    assert (body["parse_status"], body["error_code"]) == ("FAILED", "INTERNAL_ERROR")
    assert _reparse(client, pitch_id, script_id, auth_headers).status_code == 202


# ── runner: AI 호출은 백그라운드 AI 자리 하나를 쓴다 ─────────────────────


class _SlotProbeParser(FakeParser):
    """AI 를 부르는 동안 비어 있는 자리 수를 남긴다."""

    async def parse(self, script_text: str, target_ms: int | None) -> ParsedScript:
        self.free = ai_slots.background()._value
        return await super().parse(script_text, target_ms)


@pytest.mark.anyio
async def test_runner_holds_one_slot_only_while_calling_ai() -> None:
    parser = _SlotProbeParser()
    runner = ScriptParseRunner(parser, session_factory=_broken_session)
    ticket = ParseTicket(uuid.uuid7(), datetime.now(UTC), SCRIPT_TEXT, TARGET_MS)

    await runner.run(ticket)

    assert parser.free == ai_slots.BACKGROUND_LIMIT - 1
    assert ai_slots.background()._value == ai_slots.BACKGROUND_LIMIT


@pytest.mark.anyio
async def test_runner_fails_when_no_slot_frees_up(monkeypatch: pytest.MonkeyPatch) -> None:
    """자리가 없으면 AI 를 부르지 않고 AI_UNAVAILABLE 로 끝낸다. 만료(90초)까지 PENDING 으로
    두지 않는다."""
    monkeypatch.setattr(parse_runner, "SLOT_WAIT_SEC", 0.01)
    slots = ai_slots.background()
    for _ in range(ai_slots.BACKGROUND_LIMIT):
        await slots.acquire()
    parser = FakeParser()
    runner = ScriptParseRunner(parser)
    saved: list[ScriptParseErrorCode] = []

    async def record(ticket: ParseTicket, code: ScriptParseErrorCode) -> None:
        saved.append(code)

    monkeypatch.setattr(runner, "_save_failure", record)
    ticket = ParseTicket(uuid.uuid7(), datetime.now(UTC), SCRIPT_TEXT, TARGET_MS)

    await runner.run(ticket)

    assert parser.calls == []
    assert saved == [ScriptParseErrorCode.AI_UNAVAILABLE]
    assert slots._value == 0  # 얻지 못한 자리를 반납하지 않았다
