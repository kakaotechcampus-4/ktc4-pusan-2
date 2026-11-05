import asyncio
import json
import uuid
from datetime import UTC, datetime, timedelta
from functools import partial
from typing import Any

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from pitch_coach_backend.module.chat import ai_client, turn_runner
from pitch_coach_backend.module.chat.entity import (
    ChatAction,
    ChatConversation,
    ChatMessage,
    ChatRun,
    ChatRunLog,
)

META = {
    "schema_version": "1.0",
    "feature_version": "chat-agent-0.1.0",
    "model": None,
    "prompt_version": "p1",
    "corpus_version": "guides-1",
    "criteria_versions": {},
}
BUDGET = {
    "model_calls": 1,
    "tool_calls": 0,
    "elapsed_ms": 10,
    "input_tokens": 1,
    "output_tokens": 1,
}
STATE = {"v": 1, "kid": "k1", "payload": "p", "signature": "s"}


def _final(status: str = "DONE", **extra: Any) -> dict[str, Any]:
    data = {
        "status": status,
        "message": {
            "text": "답이에요",
            "claims": [{"kind": "SYSTEM", "text": "답이에요"}],
            "cards": [],
            "sources": [],
        },
        "actions": [],
        "state": None,
        "scope_pitch_id": None,
        "budget_used": BUDGET,
        "assessments": [],
        "plan": [{"step": "확인", "status": "DONE", "note": None}],
        "trace": [],
        "meta": META,
    }
    return {**data, **extra}


def _needs_action(action: dict[str, Any]) -> dict[str, Any]:
    return _final("NEEDS_ACTION", message=None, actions=[action], state=STATE)


def _stream(run_id: str, *lines: tuple[str, dict[str, Any]]) -> bytes:
    return "".join(
        json.dumps({"event": event, "run_id": run_id, "seq": seq, "data": data}) + "\n"
        for seq, (event, data) in enumerate(lines, start=1)
    ).encode()


class FakeAgent:
    def __init__(self, *replies: httpx.Response) -> None:
        self.replies = list(replies)
        self.requests: list[dict[str, Any]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(json.loads(request.content))
        return self.replies.pop(0)


def _ndjson(body: bytes) -> httpx.Response:
    return httpx.Response(200, headers={"content-type": "application/x-ndjson"}, content=body)


class _TestSession(Session):
    def connection(self, bind_arguments=None, execution_options=None):
        return super().connection(bind_arguments)


@pytest.fixture
def session_factory(db_session: Session):
    def make() -> Session:
        return _TestSession(
            bind=db_session.connection(),
            join_transaction_mode="create_savepoint",
            autoflush=False,
            expire_on_commit=False,
        )

    return make


@pytest.fixture
def agent(monkeypatch) -> FakeAgent:
    fake = FakeAgent()
    client = partial(httpx.AsyncClient, transport=httpx.MockTransport(fake))
    monkeypatch.setattr(ai_client.httpx, "AsyncClient", client)
    return fake


@pytest.fixture
def queued_run(db_session: Session, user_id: uuid.UUID) -> uuid.UUID:
    conversation = ChatConversation(user_id=user_id, title="질문")
    db_session.add(conversation)
    db_session.flush()
    message = ChatMessage(
        conversation_id=conversation.id, user_id=user_id, seq=1, role="user", text="어땠어?"
    )
    db_session.add(message)
    db_session.flush()
    now = datetime.now(UTC)
    run = ChatRun(
        conversation_id=conversation.id,
        user_id=user_id,
        message_id=message.id,
        first_trigger="USER_MESSAGE",
        status="QUEUED",
        input={
            "user_message": "어땠어?",
            "page": {"route": "home", "pitch_id": None, "take_id": None, "screen": None},
            "choice": None,
        },
        accepted_at=now,
        deadline_at=now + timedelta(seconds=120),
    )
    db_session.add(run)
    db_session.flush()
    return run.id


def _run(run_id: uuid.UUID, session_factory) -> None:
    asyncio.run(turn_runner.run(run_id, session_factory))


def _reload(db_session: Session, run_id: uuid.UUID) -> ChatRun:
    db_session.expire_all()
    return db_session.get(ChatRun, run_id)


def test_done_turn_saves_the_answer(db_session, session_factory, agent, queued_run) -> None:
    run_id = str(queued_run)
    agent.replies.append(_ndjson(_stream(run_id, ("plan", {"plan": []}), ("final", _final()))))

    _run(queued_run, session_factory)

    run = _reload(db_session, queued_run)
    assert run.status == "DONE"
    assert [call["stage"] for call in run.turn_calls] == ["DONE"]
    answer = db_session.scalar(
        select(ChatMessage).where(
            ChatMessage.conversation_id == run.conversation_id, ChatMessage.role == "assistant"
        )
    )
    assert (answer.seq, answer.text, answer.status) == (2, "답이에요", "DONE")
    log = db_session.scalar(select(ChatRunLog).where(ChatRunLog.run_id == run.id))
    assert log.status == "DONE"
    sent = agent.requests[0]
    assert sent["run"]["trigger"] == "USER_MESSAGE"
    assert sent["user_message"] == "어땠어?"
    assert sent["history"]["messages"] == []


def test_stream_error_line_fails_the_run(db_session, session_factory, agent, queued_run) -> None:
    error = {"error": {"code": "AGENT_TIMEOUT", "message": "m"}}
    agent.replies.append(_ndjson(_stream(str(queued_run), ("error", error))))

    _run(queued_run, session_factory)

    run = _reload(db_session, queued_run)
    assert (run.status, run.error_code) == ("FAILED", "AGENT_TIMEOUT")


def test_busy_before_stream_keeps_retry_after(
    db_session, session_factory, agent, queued_run
) -> None:
    agent.replies.append(
        httpx.Response(429, json={"error": {"code": "AGENT_BUSY", "message": "m"}})
    )

    _run(queued_run, session_factory)

    run = _reload(db_session, queued_run)
    assert (run.status, run.error_code, run.retry_after_s) == ("FAILED", "AGENT_BUSY", 5)


def test_failed_action_is_sent_back_and_the_turn_finishes(
    db_session, session_factory, agent, queued_run
) -> None:
    run_id = str(queued_run)
    action = {
        "action_id": "a1",
        "type": "FETCH_TAKE",
        "args": {"pitch_id": str(uuid.uuid4()), "take_id": str(uuid.uuid4())},
        "autonomy": "AUTO",
        "args_hash": None,
        "idempotency_key": f"{run_id}:1",
        "reason": "기록을 더 본다",
        "authorized_by": None,
    }
    agent.replies.append(_ndjson(_stream(run_id, ("final", _needs_action(action)))))
    agent.replies.append(_ndjson(_stream(run_id, ("final", _final()))))

    _run(queued_run, session_factory)

    run = _reload(db_session, queued_run)
    assert run.status == "DONE"
    assert [call["stage"] for call in run.turn_calls] == ["DONE", "DONE"]
    saved = db_session.scalar(select(ChatAction).where(ChatAction.run_id == queued_run))
    assert (saved.status, saved.result["error"]["code"]) == ("FAILED", "NOT_FOUND")
    resumed = agent.requests[1]
    assert resumed["run"]["trigger"] == "ACTION_RESULT"
    assert resumed["resume"]["action_results"][0]["status"] == "FAILED"


def test_answer_on_a_deleted_take_cancels_the_run(
    db_session, session_factory, agent, queued_run
) -> None:
    run_id = str(queued_run)
    deleted_take_id = str(uuid.uuid4())
    row = db_session.get(ChatRun, queued_run)
    row.input = {**row.input, "page": {**row.input["page"], "take_id": deleted_take_id}}
    db_session.flush()
    evidence = [{"evidence_id": "e1", "pitch_id": None, "take_id": deleted_take_id}]
    agent.replies.append(_ndjson(_stream(run_id, ("final", _final(evidence=evidence)))))

    _run(queued_run, session_factory)

    run = _reload(db_session, queued_run)
    assert (run.status, run.error_code) == ("CANCELLED", "AGENT_CANCELLED")
    assert (
        db_session.scalar(
            select(ChatMessage).where(
                ChatMessage.conversation_id == run.conversation_id,
                ChatMessage.role == "assistant",
            )
        )
        is None
    )


def _answer_with_pitch_source(
    db_session, session_factory, agent, queued_run, pitch_id: str
) -> ChatRun:
    run_id = str(queued_run)
    evidence = [{"evidence_id": "e1", "pitch_id": pitch_id, "take_id": None}]
    agent.replies.append(_ndjson(_stream(run_id, ("final", _final(evidence=evidence)))))

    _run(queued_run, session_factory)

    return _reload(db_session, queued_run)


def test_answer_with_malformed_source_fails_as_upstream_error(
    db_session, session_factory, agent, queued_run
) -> None:
    run = _answer_with_pitch_source(db_session, session_factory, agent, queued_run, "not-a-uuid")

    assert (run.status, run.error_code) == ("FAILED", "AGENT_UPSTREAM_ERROR")


def test_answer_with_own_take_never_sent_fails_as_upstream_error(
    db_session, session_factory, agent, queued_run, user_id
) -> None:
    from pitch_coach_backend.module.pitch.entity import Pitch, PresentationVersion, ScriptVersion
    from pitch_coach_backend.module.take.entity import Take

    pitch = Pitch(user_id=user_id, title="내 발표", time_limit_sec=300)
    db_session.add(pitch)
    db_session.flush()
    presentation = PresentationVersion(pitch_id=pitch.id, version=1, file_key=f"{pitch.id}/1.pdf")
    script = ScriptVersion(pitch_id=pitch.id, version=1, content="대본")
    db_session.add_all([presentation, script])
    db_session.flush()
    take = Take(
        pitch_id=pitch.id,
        take_number=1,
        presentation_version_id=presentation.id,
        script_version_id=script.id,
        mode="COACHING",
        script_mode="HIGHLIGHT",
        goal_time_sec=300,
        status="READY",
    )
    db_session.add(take)
    db_session.flush()
    evidence = [{"evidence_id": "e1", "pitch_id": None, "take_id": str(take.id)}]
    agent.replies.append(_ndjson(_stream(str(queued_run), ("final", _final(evidence=evidence)))))

    _run(queued_run, session_factory)

    run = _reload(db_session, queued_run)
    assert (run.status, run.error_code) == ("FAILED", "AGENT_UPSTREAM_ERROR")
    assert str(pitch.id) in run.turn_calls[0]["sources"]["pitches"]
    assert str(take.id) not in run.turn_calls[0]["sources"]["takes"]


def test_answer_with_someone_elses_pitch_fails_as_upstream_error(
    db_session, session_factory, agent, queued_run
) -> None:
    from pitch_coach_backend.module.pitch.entity import Pitch
    from pitch_coach_backend.module.user.entity import User

    other = User(email=f"{uuid.uuid4()}@example.com", name="다른 사용자")
    db_session.add(other)
    db_session.flush()
    pitch = Pitch(user_id=other.id, title="남의 발표", time_limit_sec=300)
    db_session.add(pitch)
    db_session.flush()

    run = _answer_with_pitch_source(db_session, session_factory, agent, queued_run, str(pitch.id))

    assert (run.status, run.error_code) == ("FAILED", "AGENT_UPSTREAM_ERROR")
    assert (
        db_session.scalar(
            select(ChatMessage).where(
                ChatMessage.conversation_id == run.conversation_id,
                ChatMessage.role == "assistant",
            )
        )
        is None
    )
