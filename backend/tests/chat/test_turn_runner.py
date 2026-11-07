import asyncio
import json
import logging
import uuid
from datetime import UTC, date, datetime, timedelta
from functools import partial
from typing import Any

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from pitch_coach_backend.module.chat import ai_client, turn_runner
from pitch_coach_backend.module.chat.ai_response import args_hash
from pitch_coach_backend.module.chat.entity import (
    ChatAction,
    ChatConversation,
    ChatDailyUsage,
    ChatMessage,
    ChatProfileItem,
    ChatRun,
    ChatRunLog,
)
from pitch_coach_backend.module.chat.service import turn as turn_service
from pitch_coach_backend.module.chat.service.run import SEOUL

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
MEMORY_MESSAGE = "앞으로 짧게 설명해 줘"
UPDATE = {"key": "EXPLAIN_STYLE", "value": "짧게 설명", "quote": MEMORY_MESSAGE}


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


def test_ambiguous_pitch_lookup_is_saved_as_a_finished_action(
    db_session, session_factory, agent, queued_run, user_id
) -> None:
    from pitch_coach_backend.module.pitch.entity import Pitch

    # 후보가 여럿이라 되묻는 결과도 행동은 끝까지 실행한 것이라 성공으로 둔다
    db_session.add_all(
        [Pitch(user_id=user_id, title=title, time_limit_sec=300) for title in ("발표 A", "발표 B")]
    )
    db_session.flush()
    run_id = str(queued_run)
    action = {
        "action_id": "a1",
        "type": "FETCH_PITCH",
        "args": {"pitch_id": None, "name": "발표"},
        "autonomy": "AUTO",
        "args_hash": None,
        "idempotency_key": f"{run_id}:1",
        "reason": "어느 발표인지 찾는다",
        "authorized_by": None,
    }
    agent.replies.append(_ndjson(_stream(run_id, ("final", _needs_action(action)))))
    agent.replies.append(_ndjson(_stream(run_id, ("final", _final()))))

    _run(queued_run, session_factory)

    saved = db_session.scalar(select(ChatAction).where(ChatAction.run_id == queued_run))
    assert (saved.status, saved.result["status"]) == ("SUCCEEDED", "AMBIGUOUS")
    sent = agent.requests[1]["resume"]["action_results"][0]
    assert len(sent["data"]["candidates"]) == 2


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


def test_goal_check_on_a_deleted_pitch_cancels_the_run(
    db_session, session_factory, agent, queued_run, caplog
) -> None:
    # 자동 목표 확인 실행의 범위 피치는 외래키가 없어 피치를 지워도 실행이 남는다
    take_id = str(uuid.uuid4())
    row = db_session.get(ChatRun, queued_run)
    row.first_trigger = "TAKE_REVIEWED"
    row.scope_pitch_id = uuid.uuid4()
    row.input = {
        "review_event": {"take_id": take_id, "review_revision": 1, "goal_version": 1},
        "page": {"route": "report", "pitch_id": str(row.scope_pitch_id), "take_id": take_id},
    }
    db_session.flush()

    with caplog.at_level(logging.INFO, logger=turn_runner.__name__):
        _run(queued_run, session_factory)

    run = _reload(db_session, queued_run)
    assert (run.status, run.error_code) == ("CANCELLED", "AGENT_CANCELLED")
    assert agent.requests == []
    log = db_session.scalar(select(ChatRunLog).where(ChatRunLog.run_id == run.id))
    assert log.status == "CANCELLED"
    assert not [record for record in caplog.records if record.levelno >= logging.ERROR]


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


def _remembering(db_session: Session, run_id: uuid.UUID) -> None:
    row = db_session.get(ChatRun, run_id)
    row.input = {**row.input, "user_message": MEMORY_MESSAGE}
    db_session.flush()


def _reassess(run_id: str) -> dict[str, Any]:
    args = {"take_id": str(uuid.uuid4()), "change": {"script_used": True}}
    return {
        "action_id": "a1",
        "type": "REASSESS",
        "args": args,
        "autonomy": "ASK",
        "args_hash": args_hash("REASSESS", args),
        "idempotency_key": f"{run_id}:1",
        "reason": "다시 평가",
        "authorized_by": None,
    }


def _waiting(action: dict[str, Any], **extra: Any) -> dict[str, Any]:
    message = {
        "text": "확인해 주세요",
        "claims": [{"kind": "SYSTEM", "text": "확인해 주세요"}],
        "cards": [{"type": "ASK_CONFIRM", "action_id": action["action_id"]}],
        "sources": [],
    }
    return _final("NEEDS_ACTION", message=message, actions=[action], state=STATE, **extra)


def _profile_items(db_session: Session, user_id: uuid.UUID) -> list[ChatProfileItem]:
    return list(
        db_session.scalars(select(ChatProfileItem).where(ChatProfileItem.user_id == user_id))
    )


def test_waiting_for_approval_saves_memory(
    db_session, session_factory, agent, queued_run, user_id
) -> None:
    run_id = str(queued_run)
    _remembering(db_session, queued_run)
    waiting = _waiting(_reassess(run_id), profile_updates=[UPDATE])
    agent.replies.append(_ndjson(_stream(run_id, ("final", waiting))))

    _run(queued_run, session_factory)

    run = _reload(db_session, queued_run)
    assert run.status == "WAITING_APPROVAL"
    assert [(i.key, i.value) for i in _profile_items(db_session, user_id)] == [
        ("EXPLAIN_STYLE", "짧게 설명")
    ]
    assert [card["key"] for card in run.interim_message["memory_saved"]] == ["EXPLAIN_STYLE"]


def test_same_memory_in_the_final_answer_is_carded_once(
    db_session, session_factory, agent, queued_run, user_id
) -> None:
    run_id = str(queued_run)
    _remembering(db_session, queued_run)
    waiting = _waiting(_reassess(run_id), profile_updates=[UPDATE])
    agent.replies.append(_ndjson(_stream(run_id, ("final", waiting))))
    _run(queued_run, session_factory)
    run = _reload(db_session, queued_run)
    run.status = "RUNNING"
    db_session.scalar(select(ChatAction).where(ChatAction.run_id == queued_run)).approved = True
    db_session.flush()
    agent.replies.append(_ndjson(_stream(run_id, ("final", _final(profile_updates=[UPDATE])))))

    asyncio.run(turn_runner.run(queued_run, session_factory, resume_with=turn_service.resume_point))

    answer = db_session.scalar(
        select(ChatMessage).where(ChatMessage.run_id == queued_run, ChatMessage.role == "assistant")
    )
    assert [card["type"] for card in answer.cards] == ["MEMORY_SAVED"]
    assert len(_profile_items(db_session, user_id)) == 1


def test_auto_action_reply_does_not_save_memory(
    db_session, session_factory, agent, queued_run, user_id
) -> None:
    run_id = str(queued_run)
    _remembering(db_session, queued_run)
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
    fetching = {**_needs_action(action), "profile_updates": [UPDATE]}
    agent.replies.append(_ndjson(_stream(run_id, ("final", fetching))))
    agent.replies.append(_ndjson(_stream(run_id, ("final", _final()))))

    _run(queued_run, session_factory)

    assert _reload(db_session, queued_run).status == "DONE"
    assert _profile_items(db_session, user_id) == []


def _upstream_error_without_action(db_session, queued_run) -> None:
    run = _reload(db_session, queued_run)
    assert (run.status, run.error_code) == ("FAILED", "AGENT_UPSTREAM_ERROR")
    assert db_session.scalar(select(ChatAction).where(ChatAction.run_id == queued_run)) is None


@pytest.mark.parametrize(
    "unknown",
    [
        {"state": {**STATE, "extra": 1}},
        {"budget_used": {**BUDGET, "extra": 1}},
    ],
    ids=["state", "budget_used"],
)
def test_unknown_field_in_resume_parts_fails_before_the_action(
    db_session, session_factory, agent, queued_run, unknown
) -> None:
    # 다음 호출에 되돌려 보낼 칸에 모르는 칸이 오면 행동을 실행하기 전에 응답을 거절한다
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
    reply = {**_needs_action(action), **unknown}
    agent.replies.append(_ndjson(_stream(run_id, ("final", reply))))

    _run(queued_run, session_factory)

    _upstream_error_without_action(db_session, queued_run)
    assert len(agent.requests) == 1


def test_mismatched_args_hash_fails_the_run(db_session, session_factory, agent, queued_run) -> None:
    run_id = str(queued_run)
    action = {**_reassess(run_id), "args_hash": "sha256:" + "0" * 64}
    agent.replies.append(_ndjson(_stream(run_id, ("final", _waiting(action)))))

    _run(queued_run, session_factory)

    _upstream_error_without_action(db_session, queued_run)


def test_ask_action_without_confirm_card_fails_the_run(
    db_session, session_factory, agent, queued_run
) -> None:
    run_id = str(queued_run)
    agent.replies.append(_ndjson(_stream(run_id, ("final", _needs_action(_reassess(run_id))))))

    _run(queued_run, session_factory)

    _upstream_error_without_action(db_session, queued_run)


def test_changed_args_after_approval_are_not_run(
    db_session, session_factory, agent, queued_run
) -> None:
    run_id = str(queued_run)
    action = _reassess(run_id)
    agent.replies.append(_ndjson(_stream(run_id, ("final", _waiting(action)))))
    _run(queued_run, session_factory)
    run = _reload(db_session, queued_run)
    run.status = "RUNNING"
    saved = db_session.scalar(select(ChatAction).where(ChatAction.run_id == queued_run))
    saved.approved = True
    saved.args = {**saved.args, "change": {"script_used": False}}
    db_session.flush()
    agent.replies.append(_ndjson(_stream(run_id, ("final", _final()))))

    asyncio.run(turn_runner.run(queued_run, session_factory, resume_with=turn_service.resume_point))

    sent = agent.requests[1]["resume"]["action_results"][0]
    assert (sent["status"], sent["error"]["code"]) == ("FAILED", "APPROVAL_ARGS_CHANGED")
    assert sent["executed_args_hash"] == action["args_hash"]
    db_session.expire_all()
    saved = db_session.scalar(select(ChatAction).where(ChatAction.run_id == queued_run))
    assert (saved.status, saved.result["error"]["code"]) == ("FAILED", "APPROVAL_ARGS_CHANGED")


def test_float_args_survive_storage_and_run_after_approval(
    db_session, session_factory, agent, queued_run
) -> None:
    run_id = str(queued_run)
    target = {"display": "시선", "metric": "audience_ratio", "operator": "GTE", "value": 1e16}
    args = {"take_id": str(uuid.uuid4()), "change": {"targets": [target]}}
    action = {**_reassess(run_id), "args": args, "args_hash": args_hash("REASSESS", args)}
    agent.replies.append(_ndjson(_stream(run_id, ("final", _waiting(action)))))
    _run(queued_run, session_factory)
    run = _reload(db_session, queued_run)
    run.status = "RUNNING"
    db_session.scalar(select(ChatAction).where(ChatAction.run_id == queued_run)).approved = True
    db_session.flush()
    agent.replies.append(_ndjson(_stream(run_id, ("final", _final()))))

    asyncio.run(turn_runner.run(queued_run, session_factory, resume_with=turn_service.resume_point))

    sent = agent.requests[1]["resume"]["action_results"][0]
    assert sent["error"]["code"] == "NOT_FOUND"
    assert sent["executed_args_hash"] == action["args_hash"]


def _reserve(db_session: Session) -> date:
    day = datetime.now(SEOUL).date()
    db_session.add(ChatDailyUsage(user_id=None, day=day, reserved=1))
    db_session.flush()
    return day


def _service_usage(db_session: Session, day: date) -> tuple[int, int]:
    db_session.expire_all()
    row = db_session.scalar(
        select(ChatDailyUsage).where(ChatDailyUsage.user_id.is_(None), ChatDailyUsage.day == day)
    )
    return row.reserved, row.sent


def test_sent_calls_confirm_the_service_reservation(
    db_session, session_factory, agent, queued_run
) -> None:
    run_id = str(queued_run)
    day = _reserve(db_session)
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

    assert _service_usage(db_session, day) == (0, 2)


def test_turn_reaching_the_call_limit_is_told_apart_in_the_log(
    db_session, session_factory, agent, queued_run, caplog
) -> None:
    run_id = str(queued_run)
    for call in range(1, turn_runner.MAX_TURN_CALLS + 1):
        action = {
            "action_id": f"a{call}",
            "type": "FETCH_TAKE",
            "args": {"pitch_id": str(uuid.uuid4()), "take_id": str(uuid.uuid4())},
            "autonomy": "AUTO",
            "args_hash": None,
            "idempotency_key": f"{run_id}:{call}",
            "reason": "기록을 더 본다",
            "authorized_by": None,
        }
        agent.replies.append(_ndjson(_stream(run_id, ("final", _needs_action(action)))))

    with caplog.at_level(logging.WARNING, logger=turn_runner.__name__):
        _run(queued_run, session_factory)

    db_session.expire_all()
    run = db_session.get(ChatRun, queued_run)
    assert (run.status, run.error_code) == ("FAILED", "AGENT_UPSTREAM_ERROR")
    assert len(agent.requests) == turn_runner.MAX_TURN_CALLS
    assert any("호출 한도" in record.getMessage() for record in caplog.records)


def test_args_hash_follows_the_doc_example() -> None:
    args = {
        "take_id": "t-5",
        "change": {
            "targets": [
                {
                    "display": "3장 시선 30% 넘기기",
                    "metric": "audience_ratio",
                    "operator": "GTE",
                    "value": 0.3,
                }
            ]
        },
    }

    assert args_hash("REASSESS", args).startswith("sha256:afdfa41878305b56")


def _practice_answer(run_id: str, pitch_id: str, authorized_by: str | None = None) -> dict:
    action = {
        "action_id": "p1",
        "type": "START_PRACTICE",
        "args": {"pitch_id": pitch_id},
        "autonomy": "BUTTON",
        "args_hash": None,
        "idempotency_key": f"{run_id}:p1",
        "reason": "연습을 권한다",
        "authorized_by": authorized_by,
    }
    message = {
        "text": "연습해 봐요",
        "claims": [{"kind": "SYSTEM", "text": "연습해 봐요"}],
        # AI 가 직접 넣은 버튼 카드는 버리고 BE 가 확인한 카드만 남긴다
        "cards": [{"type": "START_PRACTICE", "pitch_id": "ai-made"}, {"type": "NOTE"}],
        "sources": [],
    }
    return _final(message=message, actions=[action])


def _answer_cards(db_session: Session, run_id: uuid.UUID) -> list[dict[str, Any]]:
    answer = db_session.scalar(
        select(ChatMessage).where(ChatMessage.run_id == run_id, ChatMessage.role == "assistant")
    )
    return answer.cards


def test_start_practice_shows_a_button_for_an_owned_pitch(
    db_session, session_factory, agent, queued_run, user_id
) -> None:
    from pitch_coach_backend.module.pitch.entity import Pitch

    pitch = Pitch(user_id=user_id, title="내 발표", time_limit_sec=300)
    db_session.add(pitch)
    db_session.flush()
    run_id = str(queued_run)
    answer = _practice_answer(run_id, str(pitch.id).upper())
    agent.replies.append(_ndjson(_stream(run_id, ("final", answer))))

    _run(queued_run, session_factory)

    assert _reload(db_session, queued_run).status == "DONE"
    button = {"type": "START_PRACTICE", "pitch_id": str(pitch.id)}
    assert _answer_cards(db_session, queued_run) == [{"type": "NOTE"}, button]
    saved = db_session.scalar(select(ChatAction).where(ChatAction.run_id == queued_run))
    assert (saved.type, saved.status, saved.result) == (
        "START_PRACTICE",
        "SUCCEEDED",
        {"pitch_id": str(pitch.id)},
    )


@pytest.mark.parametrize("case", ["someone_else", "not_a_uuid", "rejected"])
def test_start_practice_without_a_usable_pitch_shows_no_button(
    db_session, session_factory, agent, queued_run, user_id, case
) -> None:
    from pitch_coach_backend.module.pitch.entity import Pitch
    from pitch_coach_backend.module.user.entity import User

    other = User(email=f"{uuid.uuid4()}@example.com", name="다른 사용자")
    db_session.add(other)
    db_session.flush()
    owner = other.id if case == "someone_else" else user_id
    pitch = Pitch(user_id=owner, title="발표", time_limit_sec=300)
    db_session.add(pitch)
    db_session.flush()
    authorized_by = None
    if case == "rejected":
        # 사용자가 연습하지 말라고 한 말을 근거로 단 버튼은 내지 않는다
        row = db_session.get(ChatRun, queued_run)
        row.input = {**row.input, "user_message": "지금은 연습 시작하지 마"}
        db_session.flush()
        authorized_by = "연습 시작하지 마"
    pitch_id = "not-a-uuid" if case == "not_a_uuid" else str(pitch.id)
    run_id = str(queued_run)
    answer = _practice_answer(run_id, pitch_id, authorized_by)
    agent.replies.append(_ndjson(_stream(run_id, ("final", answer))))

    _run(queued_run, session_factory)

    assert _reload(db_session, queued_run).status == "DONE"
    assert _answer_cards(db_session, queued_run) == [{"type": "NOTE"}]
    assert db_session.scalar(select(ChatAction).where(ChatAction.run_id == queued_run)) is None
