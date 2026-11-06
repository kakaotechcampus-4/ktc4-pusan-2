"""실행 이벤트 커서 조회 · 승인 · 거절 · 만료 · 대기 중 새 메시지 · 취소 · 최신 아님 사유."""

import asyncio
import json
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from pitch_coach_backend.module.chat import approval_sweep, turn_runner
from pitch_coach_backend.module.chat.ai_response import args_hash
from pitch_coach_backend.module.chat.entity import (
    ChatAction,
    ChatConversation,
    ChatDailyUsage,
    ChatMessage,
    ChatRun,
    ChatRunEvent,
    ChatRunLog,
)
from pitch_coach_backend.module.chat.service import run_view
from pitch_coach_backend.module.chat.service import turn as turn_service
from pitch_coach_backend.module.chat.service.accept import USER_DAILY_TURNS
from pitch_coach_backend.module.chat.service.run import OWNER, SEOUL
from pitch_coach_backend.module.chat.service.waiting import (
    CANCELLED_TEXT,
    EXPIRED_TEXT,
    NEW_MESSAGE_TEXT,
    NOT_DONE_TEXT,
)
from pitch_coach_backend.module.pitch.entity import Pitch
from pitch_coach_backend.module.user.entity import User
from tests.chat.api_helpers import bearer
from tests.chat.test_turn_runner import (  # noqa: F401
    STATE,
    _final,
    _ndjson,
    _stream,
    agent,
    session_factory,
)

pytestmark = pytest.mark.usefixtures("chat_app")

CLAIM = {
    "kind": "ASSESSMENT",
    "assessment_id": "a1",
    "text": "말씀하신 내용은 기록과 달라요",
    "evidence_ids": [],
    "source_ids": [],
    "action_id": None,
}
HOME = {"route": "home", "pitch_id": None, "take_id": None, "screen": None}


def _claim_data(evidence: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "index": 0,
        "claim": CLAIM,
        "evidence": evidence or [],
        "sources": [],
        "assessments": [{"assessment_id": "a1", "supersedes": []}],
    }


def _waiting(
    db: Session,
    user_id: uuid.UUID,
    *,
    action_type: str = "SAVE_GOAL",
    waited: timedelta = timedelta(minutes=1),
    evidence: list[dict[str, Any]] | None = None,
) -> SimpleNamespace:
    """턴 실행기가 ASK 행동에서 멈춘 상태. 거절 경로가 rollback 하므로 commit 한다."""
    now = datetime.now(UTC)
    conversation = ChatConversation(user_id=user_id, title="목표 잡기")
    db.add(conversation)
    db.flush()
    message = ChatMessage(
        conversation_id=conversation.id, user_id=user_id, seq=1, role="user", text="목표로 잡아 줘"
    )
    db.add(message)
    db.flush()
    card = {
        "type": "ASK_CONFIRM",
        "action_id": "act-1",
        "action_type": action_type,
        "title": "저장",
    }
    run = ChatRun(
        conversation_id=conversation.id,
        user_id=user_id,
        message_id=message.id,
        first_trigger="USER_MESSAGE",
        status="WAITING_APPROVAL",
        input={"user_message": "목표로 잡아 줘", "page": HOME, "choice": None},
        state={"v": 1, "kid": "k", "payload": "비밀", "signature": "s"},
        plan=[{"step": "확인", "status": "DONE", "note": None}],
        interim_message={"message": {"text": CLAIM["text"], "claims": [CLAIM], "cards": [card]}},
        budget_used={"model_calls": 1, "tool_calls": 0, "elapsed_ms": 10},
        turn_calls=[{"turn_call": 1, "request_id": "r", "state_revision": None, "stage": "DONE"}],
        row_revision=3,
        accepted_at=now - waited,
        deadline_at=now - waited + timedelta(seconds=120),
        approval_started_at=now - waited,
    )
    db.add(run)
    db.flush()
    db.add_all(
        [
            ChatRunEvent(run_id=run.id, event_seq=1, event="plan", data={"plan": run.plan}),
            ChatRunEvent(run_id=run.id, event_seq=2, event="claim", data=_claim_data(evidence)),
        ]
    )
    action = ChatAction(
        conversation_id=conversation.id,
        user_id=user_id,
        run_id=run.id,
        action_id="act-1",
        type=action_type,
        args={"expected_goal_version": 0, "goal": {}},
        args_digest="d",
        autonomy="ASK",
        idempotency_key=f"key-{run.id}",
        args_hash="hash-1",
        status="PENDING",
    )
    db.add(action)
    db.commit()
    return SimpleNamespace(conversation=conversation, run=run, action=action)


def _decide(
    client: TestClient,
    user_id: uuid.UUID,
    run_id: uuid.UUID,
    decision: str = "APPROVE",
    args_hash: str = "hash-1",
    action_id: str = "act-1",
) -> Any:
    return client.post(
        f"/api/chat/runs/{run_id}/actions/{action_id}/decision",
        json={"decision": decision, "args_hash": args_hash},
        headers=bearer(user_id),
    )


def _answers(db: Session, conversation_id: uuid.UUID) -> list[ChatMessage]:
    db.expire_all()
    return list(
        db.scalars(
            select(ChatMessage)
            .where(ChatMessage.conversation_id == conversation_id, ChatMessage.role == "assistant")
            .order_by(ChatMessage.seq)
        )
    )


# ── 실행 이벤트 커서 ─────────────────────────────────────────────────


def test_events_come_after_the_cursor_with_the_waiting_card(
    client: TestClient, db_session: Session, user_id: uuid.UUID
) -> None:
    seeded = _waiting(db_session, user_id)
    path = f"/api/chat/runs/{seeded.run.id}"

    first = client.get(path, headers=bearer(user_id)).json()
    assert [(e["event_seq"], e["event"]) for e in first["events"]] == [(1, "plan"), (2, "claim")]
    assert first["events"][1]["data"]["claim"]["text"] == CLAIM["text"]
    assert first["next_after_seq"] == 2
    assert first["has_more"] is False
    assert first["cards"] == [
        {
            "type": "ASK_CONFIRM",
            "action_id": "act-1",
            "action_type": "SAVE_GOAL",
            "title": "저장",
            "args_hash": "hash-1",
        }
    ]
    assert "비밀" not in json.dumps(first, ensure_ascii=False)

    again = client.get(f"{path}?after_seq=2", headers=bearer(user_id)).json()
    assert (again["events"], again["next_after_seq"]) == ([], 2)

    latest = client.get(
        f"/api/chat/conversations/{seeded.conversation.id}/run?after_seq=1",
        headers=bearer(user_id),
    ).json()
    assert [e["event_seq"] for e in latest["events"]] == [2]


def test_events_are_split_by_count_without_skipping(
    client: TestClient,
    db_session: Session,
    user_id: uuid.UUID,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(run_view, "EVENTS_PAGE", 1)
    seeded = _waiting(db_session, user_id)
    path = f"/api/chat/runs/{seeded.run.id}"

    page = client.get(path, headers=bearer(user_id)).json()
    assert ([e["event_seq"] for e in page["events"]], page["has_more"]) == ([1], True)
    rest = client.get(f"{path}?after_seq={page['next_after_seq']}", headers=bearer(user_id)).json()
    assert ([e["event_seq"] for e in rest["events"]], rest["has_more"]) == ([2], False)


def test_events_are_split_by_size_but_the_first_one_always_comes(
    client: TestClient,
    db_session: Session,
    user_id: uuid.UUID,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(run_view, "RESPONSE_MAX_BYTES", 10)
    seeded = _waiting(db_session, user_id)

    page = client.get(f"/api/chat/runs/{seeded.run.id}", headers=bearer(user_id)).json()

    assert ([e["event_seq"] for e in page["events"]], page["has_more"]) == ([1], True)


def test_claim_whose_source_is_gone_is_masked_but_keeps_its_place(
    client: TestClient, db_session: Session, user_id: uuid.UUID
) -> None:
    evidence = [{"evidence_id": "e1", "pitch_id": None, "take_id": str(uuid.uuid4())}]
    seeded = _waiting(db_session, user_id, evidence=evidence)

    body = client.get(f"/api/chat/runs/{seeded.run.id}", headers=bearer(user_id)).json()

    assert [(e["event_seq"], e["masked"], e["data"] is None) for e in body["events"]] == [
        (1, False, False),
        (2, True, True),
    ]
    assert body["next_after_seq"] == 2


def test_run_that_read_a_gone_source_hides_all_progress(
    client: TestClient, db_session: Session, user_id: uuid.UUID
) -> None:
    seeded = _waiting(db_session, user_id)
    seeded.run.read_sources = {"pitches": [], "takes": [str(uuid.uuid4())]}
    db_session.commit()

    body = client.get(f"/api/chat/runs/{seeded.run.id}", headers=bearer(user_id)).json()

    assert body["plan"] is None
    assert body["cards"] == []
    assert all(e["masked"] for e in body["events"])


# ── 승인 ─────────────────────────────────────────────────────────────


def test_approve_marks_the_action_and_shifts_the_deadline(
    client: TestClient,
    db_session: Session,
    user_id: uuid.UUID,
    resumed: list[uuid.UUID],
) -> None:
    seeded = _waiting(db_session, user_id, waited=timedelta(minutes=5))
    run_id = seeded.run.id
    old_deadline = seeded.run.deadline_at

    response = _decide(client, user_id, run_id)

    assert response.status_code == 200
    assert response.json()["status"] == "RUNNING"
    assert response.json()["next_after_seq"] is None
    db_session.expire_all()
    run = db_session.get(ChatRun, run_id)
    action = db_session.get(ChatAction, seeded.action.id)
    # 턴 실행기가 찾는 모양: 승인 표시가 된 PENDING 행동과 RUNNING 실행
    assert (action.approved, action.status) == (True, "PENDING")
    assert (run.status, run.row_revision) == ("RUNNING", 4)
    assert run.approval_ended_at is not None
    # 기다린 5분은 120초에 넣지 않는다
    assert run.deadline_at - old_deadline >= timedelta(minutes=5)
    assert run.deadline_at > datetime.now(UTC) + timedelta(seconds=100)
    assert resumed == [run_id]


def test_approve_takes_the_lease_so_recovery_leaves_the_run(
    client: TestClient,
    db_session: Session,
    user_id: uuid.UUID,
    resumed: list[uuid.UUID],
) -> None:
    seeded = _waiting(db_session, user_id, waited=timedelta(minutes=5))
    run_id = seeded.run.id
    # 턴 실행기가 멈춘 뒤로 임대를 늘리는 쪽이 없어 대기하는 동안 지나 있다
    seeded.run.lease_owner = "그만둔 작업자"
    seeded.run.lease_until = datetime.now(UTC) - timedelta(minutes=4)
    db_session.commit()

    assert _decide(client, user_id, run_id).status_code == 200

    db_session.expire_all()
    revision = db_session.get(ChatRun, run_id).row_revision
    assert turn_service.recover_run(db_session, run_id, datetime.now(UTC)) is None
    db_session.expire_all()
    run = db_session.get(ChatRun, run_id)
    assert (run.lease_owner, run.status, run.row_revision) == (OWNER, "RUNNING", revision)
    assert resumed == [run_id]


def test_same_approve_again_gives_the_first_result(
    client: TestClient,
    db_session: Session,
    user_id: uuid.UUID,
    resumed: list[uuid.UUID],
) -> None:
    seeded = _waiting(db_session, user_id)
    _decide(client, user_id, seeded.run.id)
    deadline = db_session.get(ChatRun, seeded.run.id).deadline_at

    # 턴 실행기가 아직 안 잡았으면 다시 띄운다(잡는 쪽이 한 번만 돈다)
    again = _decide(client, user_id, seeded.run.id)
    assert again.status_code == 200
    assert resumed == [seeded.run.id, seeded.run.id]
    db_session.expire_all()
    assert db_session.get(ChatRun, seeded.run.id).deadline_at == deadline

    # 이미 실행 중이면 띄우지 않는다. 뒤늦은 거절도 처음 결과다
    db_session.get(ChatAction, seeded.action.id).status = "RUNNING"
    db_session.commit()
    late = _decide(client, user_id, seeded.run.id, "REJECT")
    assert (late.status_code, late.json()["status"]) == (200, "RUNNING")
    assert len(resumed) == 2


def test_changed_card_is_409_and_nothing_runs(
    client: TestClient,
    db_session: Session,
    user_id: uuid.UUID,
    resumed: list[uuid.UUID],
) -> None:
    seeded = _waiting(db_session, user_id)

    response = _decide(client, user_id, seeded.run.id, args_hash="other")

    assert (response.status_code, response.json()["code"]) == (409, "APPROVAL_HASH_MISMATCH")
    assert resumed == []
    db_session.expire_all()
    assert db_session.get(ChatRun, seeded.run.id).status == "WAITING_APPROVAL"


def test_only_my_ask_action_can_be_decided(
    client: TestClient, db_session: Session, user_id: uuid.UUID
) -> None:
    seeded = _waiting(db_session, user_id)
    other = User(email=f"{uuid.uuid4()}@example.com", name="다른 사용자")
    db_session.add(other)
    auto = ChatAction(
        conversation_id=seeded.conversation.id,
        user_id=user_id,
        run_id=seeded.run.id,
        action_id="act-auto",
        type="FETCH_TAKE",
        args={},
        args_digest="d",
        autonomy="AUTO",
        idempotency_key="key-auto",
        status="PENDING",
    )
    db_session.add(auto)
    db_session.commit()

    assert _decide(client, other.id, seeded.run.id).status_code == 404
    assert _decide(client, user_id, uuid.uuid4()).status_code == 404
    assert _decide(client, user_id, seeded.run.id, action_id="none").status_code == 404
    assert _decide(client, user_id, seeded.run.id, action_id="act-auto").status_code == 404


def test_args_are_not_accepted_in_a_decision(
    client: TestClient, db_session: Session, user_id: uuid.UUID
) -> None:
    seeded = _waiting(db_session, user_id)

    response = client.post(
        f"/api/chat/runs/{seeded.run.id}/actions/act-1/decision",
        json={"decision": "APPROVE", "args_hash": "hash-1", "args": {"goal": {}}},
        headers=bearer(user_id),
    )

    assert response.status_code == 422


# ── 거절 ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize("action_type", ["SAVE_GOAL", "REASSESS"])
def test_reject_keeps_the_emitted_sentences_and_adds_the_fixed_one(
    client: TestClient,
    db_session: Session,
    user_id: uuid.UUID,
    resumed: list[uuid.UUID],
    action_type: str,
) -> None:
    seeded = _waiting(db_session, user_id, action_type=action_type)
    run_id = seeded.run.id

    response = _decide(client, user_id, run_id, "REJECT")

    assert (response.status_code, response.json()["status"]) == (200, "DONE")
    assert resumed == []
    [answer] = _answers(db_session, seeded.conversation.id)
    fixed = NOT_DONE_TEXT[action_type]
    assert answer.text == f"{CLAIM['text']}\n{fixed}"
    assert [claim["text"] for claim in answer.claims] == [CLAIM["text"], fixed]
    assert (answer.status, answer.run_id, answer.seq) == ("DONE", run_id, 2)
    assert answer.assessments == [{"assessment_id": "a1", "supersedes": []}]
    action = db_session.get(ChatAction, seeded.action.id)
    assert (action.status, action.approved) == ("REJECTED", False)
    assert db_session.scalar(select(ChatRunLog.status).where(ChatRunLog.run_id == run_id)) == "DONE"
    assert db_session.get(ChatRun, run_id).state is None

    # 같은 요청을 다시 보내도 메시지는 하나다
    assert _decide(client, user_id, run_id, "REJECT").json()["status"] == "DONE"
    assert len(_answers(db_session, seeded.conversation.id)) == 1


def test_reject_does_not_bring_back_a_deleted_source(
    client: TestClient, db_session: Session, user_id: uuid.UUID
) -> None:
    evidence = [{"evidence_id": "e1", "pitch_id": None, "take_id": str(uuid.uuid4())}]
    seeded = _waiting(db_session, user_id, evidence=evidence)

    _decide(client, user_id, seeded.run.id, "REJECT")

    [answer] = _answers(db_session, seeded.conversation.id)
    assert answer.text == NOT_DONE_TEXT["SAVE_GOAL"]
    assert (answer.evidence, answer.assessments) == ([], [])


# ── 만료 ─────────────────────────────────────────────────────────────


def test_approve_after_30_minutes_expires_instead(
    client: TestClient,
    db_session: Session,
    user_id: uuid.UUID,
    resumed: list[uuid.UUID],
) -> None:
    seeded = _waiting(db_session, user_id, waited=timedelta(minutes=31))

    response = _decide(client, user_id, seeded.run.id)

    assert (response.status_code, response.json()["code"]) == (409, "AGENT_EXPIRED")
    assert resumed == []
    [answer] = _answers(db_session, seeded.conversation.id)
    assert answer.text == f"{CLAIM['text']}\n{EXPIRED_TEXT}"
    assert answer.status == "PARTIAL"
    assert db_session.get(ChatRun, seeded.run.id).status == "EXPIRED"
    assert db_session.get(ChatAction, seeded.action.id).status == "EXPIRED"

    # 끝난 뒤의 결정도 같은 코드다
    assert _decide(client, user_id, seeded.run.id).json()["code"] == "AGENT_EXPIRED"


def test_sweep_expires_only_overdue_waits(db_session: Session, user_id: uuid.UUID) -> None:
    overdue = _waiting(db_session, user_id, waited=timedelta(minutes=31))
    fresh = _waiting(db_session, user_id, waited=timedelta(minutes=5))

    def factory() -> Session:
        return Session(
            bind=db_session.connection(),
            join_transaction_mode="create_savepoint",
            autoflush=False,
            expire_on_commit=False,
        )

    assert asyncio.run(approval_sweep.sweep_once(factory)) == 1

    db_session.expire_all()
    assert db_session.get(ChatRun, overdue.run.id).status == "EXPIRED"
    assert db_session.get(ChatRun, overdue.run.id).error_code == "AGENT_EXPIRED"
    assert db_session.get(ChatRun, fresh.run.id).status == "WAITING_APPROVAL"
    assert [m.text for m in _answers(db_session, overdue.conversation.id)] == [
        f"{CLAIM['text']}\n{EXPIRED_TEXT}"
    ]
    # 다시 돌아도 한 번만 끝낸다
    assert asyncio.run(approval_sweep.sweep_once(factory)) == 0


# ── 대기 중 새 메시지 · 취소 ─────────────────────────────────────────


def _send(client: TestClient, user_id: uuid.UUID, conversation_id: uuid.UUID) -> Any:
    return client.post(
        "/api/chat/messages",
        json={
            "conversation_id": str(conversation_id),
            "text": "다른 질문이요",
            "page": HOME,
            "client_request_id": str(uuid.uuid4()),
        },
        headers=bearer(user_id),
    )


def test_new_message_cancels_the_wait_and_is_accepted(
    client: TestClient,
    db_session: Session,
    user_id: uuid.UUID,
    chat_app: list[uuid.UUID],
) -> None:
    seeded = _waiting(db_session, user_id)

    response = _send(client, user_id, seeded.conversation.id)

    assert response.status_code == 202
    [answer] = _answers(db_session, seeded.conversation.id)
    assert answer.text == f"{CLAIM['text']}\n{NEW_MESSAGE_TEXT}"
    assert answer.seq == 2
    run = db_session.get(ChatRun, seeded.run.id)
    assert (run.status, run.error_code) == ("CANCELLED", "AGENT_CANCELLED")
    assert db_session.get(ChatAction, seeded.action.id).status == "CANCELLED"
    new_message = db_session.get(ChatMessage, uuid.UUID(response.json()["message_id"]))
    assert new_message.seq == 3
    assert chat_app == [uuid.UUID(response.json()["run_id"])]

    # 취소가 확정된 뒤 온 승인은 실행하지 않는다
    late = _decide(client, user_id, seeded.run.id)
    assert (late.status_code, late.json()["code"]) == (409, "AGENT_CANCELLED")


def test_new_message_over_the_limit_leaves_the_wait_alone(
    client: TestClient, db_session: Session, user_id: uuid.UUID
) -> None:
    seeded = _waiting(db_session, user_id)
    day = datetime.now(UTC).astimezone(SEOUL).date()
    db_session.add(ChatDailyUsage(user_id=user_id, day=day, turns=USER_DAILY_TURNS))
    db_session.commit()

    response = _send(client, user_id, seeded.conversation.id)

    assert response.status_code == 429
    db_session.expire_all()
    assert db_session.get(ChatRun, seeded.run.id).status == "WAITING_APPROVAL"
    assert db_session.get(ChatAction, seeded.action.id).status == "PENDING"
    assert _answers(db_session, seeded.conversation.id) == []


def test_cancel_during_the_wait_closes_the_action_and_keeps_sentences(
    client: TestClient, db_session: Session, user_id: uuid.UUID
) -> None:
    seeded = _waiting(db_session, user_id)

    response = client.post(f"/api/chat/runs/{seeded.run.id}/cancel", headers=bearer(user_id))

    assert response.json()["status"] == "CANCELLED"
    assert response.json()["error"] == {"code": "AGENT_CANCELLED"}
    assert response.json()["cards"] == []
    [answer] = _answers(db_session, seeded.conversation.id)
    # 새 메시지로 취소될 때와 다른 문장이다(7-10)
    assert answer.text == f"{CLAIM['text']}\n{CANCELLED_TEXT}"
    assert db_session.get(ChatAction, seeded.action.id).status == "CANCELLED"


# ── 턴 실행기까지 ────────────────────────────────────────────────────


def test_full_flow_hold_card_approve_resume_done(
    client: TestClient,
    db_session: Session,
    user_id: uuid.UUID,
    session_factory,  # noqa: F811
    agent,  # noqa: F811
    resumed: list[uuid.UUID],
) -> None:
    sent = client.post(
        "/api/chat/messages",
        json={
            "text": "목표로 잡아 줘",
            "page": HOME,
            "client_request_id": str(uuid.uuid4()),
        },
        headers=bearer(user_id),
    ).json()
    run_id = uuid.UUID(sent["run_id"])
    card = {"type": "ASK_CONFIRM", "action_id": "act-1", "action_type": "SAVE_GOAL"}
    args = {"expected_goal_version": 0, "goal": {"metric": "없는 지표"}}
    shown_hash = args_hash("SAVE_GOAL", args)
    action = {
        "action_id": "act-1",
        "type": "SAVE_GOAL",
        "args": args,
        "autonomy": "ASK",
        "args_hash": shown_hash,
        "idempotency_key": "k-1",
        "reason": "목표 저장",
        "authorized_by": "목표로 잡아 줘",
    }
    message = {"text": CLAIM["text"], "claims": [CLAIM], "cards": [card], "sources": []}
    agent.replies.append(
        _ndjson(
            _stream(
                str(run_id),
                ("claim", _claim_data()),
                ("final", _final("NEEDS_ACTION", message=message, actions=[action], state=STATE)),
            )
        )
    )

    asyncio.run(turn_runner.run(run_id, session_factory))

    db_session.expire_all()
    body = client.get(f"/api/chat/runs/{run_id}", headers=bearer(user_id)).json()
    assert body["status"] == "WAITING_APPROVAL"
    assert [e["event"] for e in body["events"]] == ["claim"]
    assert body["cards"][0]["args_hash"] == shown_hash

    decided = _decide(client, user_id, run_id, args_hash=shown_hash)
    assert decided.json()["status"] == "RUNNING"
    assert resumed == [run_id]
    # 최종 답은 이 실행에서 이미 낸 문장으로 시작한다(다르면 PARTIAL, 12-3 '최종 응답 검사')
    closing = {**CLAIM, "text": "기록과 맞지 않아 저장하지 못했어요."}
    final_message = {
        "text": f"{CLAIM['text']}\n{closing['text']}",
        "claims": [CLAIM, closing],
        "cards": [],
        "sources": [],
    }
    agent.replies.append(_ndjson(_stream(str(run_id), ("final", _final(message=final_message)))))

    asyncio.run(turn_runner.run(run_id, session_factory, resume_with=turn_service.resume_point))

    db_session.expire_all()
    assert db_session.get(ChatRun, run_id).status == "DONE"
    assert agent.requests[1]["resume"]["action_results"][0]["action_id"] == "act-1"
    # 승인한 인자 그대로 실행했다(이 인자는 기록과 맞지 않아 실패 결과로 돌아간다)
    assert db_session.scalar(select(ChatAction.status).where(ChatAction.run_id == run_id)) == (
        "FAILED"
    )


# ── 최신 아님 사유 ───────────────────────────────────────────────────


def test_messages_and_list_show_stale_reasons(
    client: TestClient, db_session: Session, user_id: uuid.UUID
) -> None:
    pitch = Pitch(user_id=user_id, title="졸업 발표", time_limit_sec=300)
    db_session.add(pitch)
    db_session.flush()
    conversation = ChatConversation(user_id=user_id, title="질문", scope_pitch_id=pitch.id)
    db_session.add(conversation)
    db_session.flush()
    pitch_id = str(pitch.id)
    basis = {
        "pitches": [
            {
                "pitch_id": pitch_id,
                "script_version_id": None,
                "presentation_version_id": None,
                "standard_version": None,
                "takes": [],
            }
        ],
        "conversation_goal_version": 0,
    }
    db_session.add_all(
        [
            ChatMessage(
                conversation_id=conversation.id,
                user_id=user_id,
                seq=1,
                role="assistant",
                text="옛 답",
                status="DONE",
                basis=basis,
                source_pitch_ids=[pitch_id],
            ),
            ChatMessage(
                conversation_id=conversation.id,
                user_id=user_id,
                seq=2,
                role="assistant",
                text="지워진 근거의 답",
                status="DONE",
                source_take_ids=[str(uuid.uuid4())],
            ),
        ]
    )
    conversation.goal_version = 1
    db_session.flush()

    messages = client.get(
        f"/api/chat/conversations/{conversation.id}/messages", headers=bearer(user_id)
    ).json()["messages"]
    assert "GOAL_CHANGED" in messages[0]["stale_reasons"]
    assert messages[1]["stale_reasons"] == ["SOURCE_DELETED"]

    listed = client.get("/api/chat/conversations", headers=bearer(user_id)).json()["conversations"]
    assert listed[0]["stale"] is True


def test_conversation_without_answer_is_not_stale(client: TestClient, user_id: uuid.UUID) -> None:
    client.post("/api/chat/conversations", json={}, headers=bearer(user_id))

    listed = client.get("/api/chat/conversations", headers=bearer(user_id)).json()["conversations"]

    assert listed[0]["stale"] is False
