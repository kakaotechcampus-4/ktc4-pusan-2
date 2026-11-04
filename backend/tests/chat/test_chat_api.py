"""코치 대화 API(화면 ↔ BE). 턴 실행기는 띄우지 않고 띄우려 한 run_id 만 모은다."""

import asyncio
import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from pitch_coach_backend.core.security import create_access_token
from pitch_coach_backend.module.chat.api_repository import ChatApiRepository
from pitch_coach_backend.module.chat.entity import (
    ChatConversation,
    ChatDailyUsage,
    ChatLease,
    ChatMessage,
    ChatRun,
    ChatRunLog,
)
from pitch_coach_backend.module.chat.service.accept import USER_DAILY_TURNS, seconds_until_tomorrow
from pitch_coach_backend.module.chat.service.run import SEOUL
from pitch_coach_backend.module.chat.service.stale import MASKED_TEXT
from pitch_coach_backend.module.pitch.entity import Pitch
from pitch_coach_backend.module.pitch.repository import PitchRepository
from pitch_coach_backend.module.user.entity import User

HOME = {"route": "home", "pitch_id": None, "take_id": None, "screen": None}


async def _no_sweep_stop() -> None:
    return None


def _no_sweep() -> Any:
    return _no_sweep_stop


@pytest.fixture(autouse=True)
def chat_app(monkeypatch: pytest.MonkeyPatch) -> list[uuid.UUID]:
    """대화를 켜고, 턴 실행기 · 대화 주기 작업이 실제 DB · AI 를 부르지 않게 막는다."""
    from pitch_coach_backend import main
    from pitch_coach_backend.core.config import settings
    from pitch_coach_backend.module.chat import controller

    monkeypatch.setattr(settings, "agent_enabled", True)
    # 대화 주기 작업은 SessionLocal(테스트 DB 가 아니다)로 돈다
    monkeypatch.setattr(main, "start_goal_sweep", _no_sweep)
    monkeypatch.setattr(main, "start_cleanup_sweep", _no_sweep)
    started: list[uuid.UUID] = []
    monkeypatch.setattr(controller.turn_runner, "start", started.append)
    return started


def _headers(user_id: uuid.UUID) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(user_id)}"}


def _other_user(db: Session) -> uuid.UUID:
    user = User(email=f"{uuid.uuid4()}@example.com", name="다른 사용자")
    db.add(user)
    db.flush()
    return user.id


def _pitch(db: Session, user_id: uuid.UUID, title: str = "졸업 발표") -> uuid.UUID:
    pitch = Pitch(user_id=user_id, title=title, time_limit_sec=300)
    db.add(pitch)
    db.flush()
    return pitch.id


def _send(client: TestClient, user_id: uuid.UUID, **overrides: Any) -> tuple[int, dict[str, Any]]:
    body = {
        "conversation_id": None,
        "text": "3장에서 왜 시선이 떨어져?",
        "page": HOME,
        "client_request_id": str(uuid.uuid4()),
        **overrides,
    }
    response = client.post("/api/chat/messages", json=body, headers=_headers(user_id))
    return response.status_code, response.json()


def test_availability_follows_the_switch(
    client: TestClient, user_id: uuid.UUID, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pitch_coach_backend.core.config import settings

    assert client.get("/api/chat/availability", headers=_headers(user_id)).json() == {
        "enabled": True
    }
    monkeypatch.setattr(settings, "agent_enabled", False)
    assert client.get("/api/chat/availability", headers=_headers(user_id)).json() == {
        "enabled": False
    }
    status, body = _send(client, user_id)
    assert (status, body["code"]) == (503, "AGENT_DISABLED")


def test_chat_api_needs_login(client: TestClient) -> None:
    assert client.get("/api/chat/availability").status_code == 401
    assert client.get("/api/chat/conversations").status_code == 401


def test_first_message_creates_conversation_and_queued_run(
    client: TestClient, db_session: Session, user_id: uuid.UUID, chat_app: list[uuid.UUID]
) -> None:
    pitch_id = _pitch(db_session, user_id)
    status, body = _send(
        client,
        user_id,
        scope_pitch_id=str(pitch_id),
        page={"route": "report", "pitch_id": str(pitch_id), "take_id": None, "screen": None},
    )

    assert status == 202
    assert body["status"] == "QUEUED"
    run = db_session.get(ChatRun, uuid.UUID(body["run_id"]))
    conversation = db_session.get(ChatConversation, uuid.UUID(body["conversation_id"]))
    message = db_session.get(ChatMessage, uuid.UUID(body["message_id"]))
    assert (run.status, run.first_trigger, run.message_id) == ("QUEUED", "USER_MESSAGE", message.id)
    assert run.scope_pitch_id == pitch_id
    # 턴 실행기가 처음 요청을 다시 만들 원래 입력
    assert run.input["user_message"] == "3장에서 왜 시선이 떨어져?"
    assert run.input["page"]["pitch_id"] == str(pitch_id)
    assert run.deadline_at - run.accepted_at == timedelta(seconds=120)
    assert (message.role, message.seq, message.run_id) == ("user", 1, run.id)
    assert conversation.scope_pitch_id == pitch_id
    assert conversation.title == "3장에서 왜 시선이 떨어져?"
    assert chat_app == [run.id]


def test_long_title_is_cut_to_first_part(client: TestClient, user_id: uuid.UUID) -> None:
    _, body = _send(client, user_id, text="가" * 100)
    listed = client.get("/api/chat/conversations", headers=_headers(user_id)).json()

    title = listed["conversations"][0]["title"]
    assert listed["conversations"][0]["conversation_id"] == body["conversation_id"]
    assert len(title) == 40
    assert title.endswith("…")


def test_same_request_id_returns_the_first_result(client: TestClient, user_id: uuid.UUID) -> None:
    request_id = str(uuid.uuid4())
    first = _send(client, user_id, client_request_id=request_id)
    again = _send(client, user_id, client_request_id=request_id)

    assert first == again


def test_replay_starts_the_turn_only_while_it_is_still_queued(
    client: TestClient, db_session: Session, user_id: uuid.UUID, chat_app: list[uuid.UUID]
) -> None:
    # 커밋은 됐는데 응답을 잃으면 턴 실행기를 띄우지 못한 채 남는다. 다시 보낸 요청이 띄운다
    request_id = str(uuid.uuid4())
    _, first = _send(client, user_id, client_request_id=request_id)
    _send(client, user_id, client_request_id=request_id)
    run_id = uuid.UUID(first["run_id"])
    assert chat_app == [run_id, run_id]

    db_session.get(ChatRun, run_id).status = "RUNNING"
    db_session.flush()
    _send(client, user_id, client_request_id=request_id)

    assert chat_app == [run_id, run_id]


def test_transient_db_error_before_commit_is_503_and_saves_nothing(
    client: TestClient,
    db_session: Session,
    user_id: uuid.UUID,
    monkeypatch: pytest.MonkeyPatch,
    chat_app: list[uuid.UUID],
) -> None:
    def lost(*args: Any, **kwargs: Any) -> bool:
        raise OperationalError("UPDATE chat_daily_usage", {}, Exception("connection lost"))

    monkeypatch.setattr(ChatApiRepository, "charge_turn", lost)

    status, body = _send(client, user_id)

    assert (status, body["code"]) == (503, "AGENT_NOT_ACCEPTED")
    assert body["message"] == "요청을 접수하지 못했으니 다시 보내 주세요."
    conversations = select(ChatConversation).where(ChatConversation.user_id == user_id)
    assert db_session.scalars(conversations).all() == []
    assert chat_app == []


def test_same_request_id_with_other_text_is_conflict(
    client: TestClient, user_id: uuid.UUID
) -> None:
    request_id = str(uuid.uuid4())
    _send(client, user_id, client_request_id=request_id)
    status, body = _send(client, user_id, client_request_id=request_id, text="다른 질문")

    assert (status, body["code"]) == (409, "AGENT_REQUEST_CONFLICT")


def test_replay_is_answered_before_the_running_check(
    client: TestClient, user_id: uuid.UUID
) -> None:
    # 응답을 잃고 다시 보낸 요청이 '진행 중' 409 로 막히면 안 된다.
    # 기존 대화로 보내야 진행 중 검사를 거친다
    created = client.post("/api/chat/conversations", json={}, headers=_headers(user_id)).json()
    body = {"conversation_id": created["conversation_id"], "client_request_id": str(uuid.uuid4())}
    _, first = _send(client, user_id, **body)
    status, again = _send(client, user_id, **body)

    assert status == 202
    assert again["run_id"] == first["run_id"]


def _miss_first_lookup(monkeypatch: pytest.MonkeyPatch) -> None:
    """같은 키를 동시에 보낸 뒤 요청처럼, 맨 앞 재전송 조회만 앞 요청을 못 보게 한다."""
    real = ChatApiRepository.find_request
    calls: list[uuid.UUID] = []

    def miss_once(self: ChatApiRepository, user_id: uuid.UUID, key: uuid.UUID) -> Any:
        calls.append(key)
        return None if len(calls) == 1 else real(self, user_id, key)

    monkeypatch.setattr(ChatApiRepository, "find_request", miss_once)


def test_same_key_seen_only_after_the_conversation_lock_is_replayed(
    client: TestClient, user_id: uuid.UUID, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 대화 잠금을 얻고 나면 앞 요청의 실행이 보여 진행 중으로 막힌다. 이미 접수된 메시지다
    created = client.post("/api/chat/conversations", json={}, headers=_headers(user_id)).json()
    body = {"conversation_id": created["conversation_id"], "client_request_id": str(uuid.uuid4())}
    _, first = _send(client, user_id, **body)
    _miss_first_lookup(monkeypatch)

    status, again = _send(client, user_id, **body)

    assert (status, again.get("run_id")) == (202, first["run_id"])


def test_same_key_seen_only_after_the_daily_limit_is_replayed(
    client: TestClient, db_session: Session, user_id: uuid.UUID, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 새 대화로 보낸 앞 요청이 그날 마지막 턴을 썼다. 뒤 요청은 메시지 유일 제약까지 가지 못한다
    request_id = str(uuid.uuid4())
    _, first = _send(client, user_id, client_request_id=request_id)
    day = datetime.now(UTC).astimezone(SEOUL).date()
    db_session.execute(
        update(ChatDailyUsage)
        .where(ChatDailyUsage.user_id == user_id, ChatDailyUsage.day == day)
        .values(turns=USER_DAILY_TURNS)
    )
    # 접수가 rollback 하는 경로다. 준비 데이터가 함께 되돌아가지 않게 commit 한다
    db_session.commit()
    _miss_first_lookup(monkeypatch)

    status, again = _send(client, user_id, client_request_id=request_id)

    assert (status, again.get("run_id")) == (202, first["run_id"])


def test_new_message_while_running_is_rejected(client: TestClient, user_id: uuid.UUID) -> None:
    _, first = _send(client, user_id)
    status, body = _send(client, user_id, conversation_id=first["conversation_id"])

    assert (status, body["code"]) == (409, "AGENT_RUN_IN_PROGRESS")


def test_active_run_race_is_409(
    client: TestClient, user_id: uuid.UUID, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 실행 중 검사를 지난 뒤 다른 탭이 같은 대화에 먼저 실행을 만든 경우다.
    # 부분 유일 인덱스가 막는다
    _, first = _send(client, user_id)
    monkeypatch.setattr(ChatApiRepository, "has_active_run", lambda self, conversation_id: False)

    status, body = _send(client, user_id, conversation_id=first["conversation_id"])

    assert (status, body["code"]) == (409, "AGENT_RUN_IN_PROGRESS")


def test_other_integrity_error_is_not_reported_as_running(
    client: TestClient, user_id: uuid.UUID, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 소유권을 확인한 뒤 피치가 지워진 경우다(외래 키). 진행 중으로 바꿔 말하지 않는다
    monkeypatch.setattr(PitchRepository, "get_owned", lambda self, pitch_id, user_id: object())

    with pytest.raises(IntegrityError):
        _send(client, user_id, scope_pitch_id=str(uuid.uuid4()))


def test_next_message_after_finish_gets_next_seq(
    client: TestClient, db_session: Session, user_id: uuid.UUID
) -> None:
    _, first = _send(client, user_id)
    db_session.get(ChatRun, uuid.UUID(first["run_id"])).status = "DONE"
    db_session.flush()

    status, second = _send(client, user_id, conversation_id=first["conversation_id"])

    assert status == 202
    assert second["conversation_id"] == first["conversation_id"]
    assert db_session.get(ChatMessage, uuid.UUID(second["message_id"])).seq == 2


@pytest.mark.parametrize("text", ["", "   \n", "가" * 2001])
def test_text_out_of_range_is_422(client: TestClient, user_id: uuid.UUID, text: str) -> None:
    status, body = _send(client, user_id, text=text)

    assert (status, body["code"]) == (422, "VALIDATION_ERROR")


def _nested(depth: int) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for _ in range(depth):
        value = {"x": value}
    return value


@pytest.mark.parametrize(
    "screen", [{"x": float("nan")}, {"x": "a\x00b"}, {"a\x00": 1}, _nested(33)]
)
def test_unstorable_screen_is_422(
    client: TestClient, user_id: uuid.UUID, screen: dict[str, Any]
) -> None:
    # 실행의 원래 입력(JSONB)에 저장하지 못해 500 이 나기 전에 막는다.
    # httpx 는 NaN 을 보내지 않으므로 본문을 직접 만든다(JSON 파서는 NaN 을 받는다)
    body = {
        "text": "질문",
        "page": {**HOME, "screen": screen},
        "client_request_id": str(uuid.uuid4()),
    }
    response = client.post(
        "/api/chat/messages",
        content=json.dumps(body),
        headers={**_headers(user_id), "Content-Type": "application/json"},
    )

    assert (response.status_code, response.json()["code"]) == (422, "VALIDATION_ERROR")


def test_text_at_limit_is_kept_whole(
    client: TestClient, db_session: Session, user_id: uuid.UUID
) -> None:
    text = "가" * 2000
    _, body = _send(client, user_id, text=text)

    assert db_session.get(ChatMessage, uuid.UUID(body["message_id"])).text == text


def test_others_targets_are_the_same_404(
    client: TestClient, db_session: Session, user_id: uuid.UUID
) -> None:
    other = _other_user(db_session)
    _, theirs = _send(client, other)
    their_pitch = _pitch(db_session, other)

    cases = [
        {"conversation_id": theirs["conversation_id"]},
        {"scope_pitch_id": str(their_pitch)},
        {"page": {**HOME, "pitch_id": str(their_pitch)}},
        {"page": {**HOME, "take_id": str(uuid.uuid4())}},
    ]
    for overrides in cases:
        status, body = _send(client, user_id, **overrides)
        assert (status, body["code"]) == (404, "NOT_FOUND"), overrides

    run_id = theirs["run_id"]
    conversation_id = theirs["conversation_id"]
    for path in (
        f"/api/chat/runs/{run_id}",
        f"/api/chat/conversations/{conversation_id}/messages",
        f"/api/chat/conversations/{conversation_id}/run",
    ):
        assert client.get(path, headers=_headers(user_id)).status_code == 404, path
    cancel = client.post(f"/api/chat/runs/{run_id}/cancel", headers=_headers(user_id))
    assert cancel.status_code == 404


def test_again_of_and_choice_must_belong_to_the_conversation(
    client: TestClient, user_id: uuid.UUID
) -> None:
    _, other_conversation = _send(client, user_id)
    status, _ = _send(client, user_id, again_of_message_id=other_conversation["message_id"])
    assert status == 404

    choice = {"proposal_id": str(uuid.uuid4()), "option": "USER"}
    status, _ = _send(client, user_id, choice=choice)
    assert status == 404


def test_choice_of_an_option_not_on_the_card_is_404(
    client: TestClient, db_session: Session, user_id: uuid.UUID
) -> None:
    # 잴 수 없는 사용자 안은 선택지에 두지 않아 카드에 추천 안만 있을 수 있다
    _, sent = _send(client, user_id)
    conversation_id = uuid.UUID(sent["conversation_id"])
    db_session.get(ChatRun, uuid.UUID(sent["run_id"])).status = "DONE"
    proposal_id = str(uuid.uuid4())
    card = {
        "type": "GOAL_PROPOSAL",
        "proposal_id": proposal_id,
        "options": [{"label": "EXPERT", "goal": {"metric": "pace"}, "content_hash": "h"}],
        "evidence_basis": {"records": [], "sources": [], "screens": []},
    }
    _answer(db_session, conversation_id, user_id, 2, cards=[card])
    # 접수가 rollback 하는 경로다. 준비 데이터가 함께 되돌아가지 않게 commit 한다
    db_session.commit()

    def choose(option: str) -> tuple[int, dict[str, Any]]:
        return _send(
            client,
            user_id,
            conversation_id=str(conversation_id),
            choice={"proposal_id": proposal_id, "option": option},
        )

    status, body = choose("USER")
    assert (status, body["code"]) == (404, "NOT_FOUND")
    assert choose("EXPERT")[0] == 202


def test_daily_limit_is_429_with_retry_after_and_charges_nothing(
    client: TestClient, db_session: Session, user_id: uuid.UUID
) -> None:
    day = datetime.now(UTC).astimezone(SEOUL).date()
    db_session.add(ChatDailyUsage(user_id=user_id, day=day, turns=USER_DAILY_TURNS))
    # 접수가 rollback 하는 경로다. 준비 데이터가 함께 되돌아가지 않게 commit 한다
    db_session.commit()
    reserved = select(ChatDailyUsage.reserved).where(
        ChatDailyUsage.user_id.is_(None), ChatDailyUsage.day == day
    )
    before = db_session.scalar(reserved)

    status, body = _send(client, user_id)

    assert (status, body["code"]) == (429, "AGENT_DAILY_LIMIT")
    # 실패한 접수는 대화 · 메시지 · 서비스 전체 예약을 남기지 않는다
    db_session.expire_all()
    conversations = select(ChatConversation).where(ChatConversation.user_id == user_id)
    assert db_session.scalars(conversations).all() == []
    assert db_session.scalar(reserved) == before


def test_daily_limit_has_retry_after_header(
    client: TestClient, db_session: Session, user_id: uuid.UUID
) -> None:
    day = datetime.now(UTC).astimezone(SEOUL).date()
    db_session.add(ChatDailyUsage(user_id=user_id, day=day, turns=USER_DAILY_TURNS))
    db_session.commit()

    response = client.post(
        "/api/chat/messages",
        json={"text": "질문", "page": HOME, "client_request_id": str(uuid.uuid4())},
        headers=_headers(user_id),
    )

    assert 0 < int(response.headers["retry-after"]) <= 86_400


def test_turns_are_counted_per_message(
    client: TestClient, db_session: Session, user_id: uuid.UUID
) -> None:
    _send(client, user_id)
    _send(client, user_id)
    day = datetime.now(UTC).astimezone(SEOUL).date()

    turns = db_session.scalar(
        select(ChatDailyUsage.turns).where(
            ChatDailyUsage.user_id == user_id, ChatDailyUsage.day == day
        )
    )
    assert turns == 2


def test_seconds_until_tomorrow_uses_seoul_midnight() -> None:
    # 서울 23:59:30 → 30초
    now = datetime(2026, 11, 5, 14, 59, 30, tzinfo=UTC)
    assert seconds_until_tomorrow(now) == 30
    # 0.5초가 남으면 올려 1초다. 내리면 자정 전에 다시 보낸다
    assert seconds_until_tomorrow(now.replace(second=59, microsecond=500_000)) == 1


def test_frontend_on_another_origin_can_read_retry_after(
    client: TestClient, user_id: uuid.UUID
) -> None:
    from pitch_coach_backend.core.config import settings

    origin = settings.frontend_base_url.rstrip("/")
    response = client.get("/api/chat/availability", headers={**_headers(user_id), "Origin": origin})

    assert response.headers["access-control-expose-headers"] == "Retry-After"


def test_run_status_hides_signed_state_and_error_message(
    client: TestClient, db_session: Session, user_id: uuid.UUID
) -> None:
    _, sent = _send(client, user_id)
    run = db_session.get(ChatRun, uuid.UUID(sent["run_id"]))
    run.status = "FAILED"
    run.state = {"payload": "비밀", "signature": "s"}
    run.plan = [{"step": "기록 확인", "status": "DONE", "note": None}]
    run.error_code = "AGENT_BUSY"
    run.retry_after_s = 5
    db_session.flush()

    body = client.get(f"/api/chat/runs/{run.id}", headers=_headers(user_id)).json()

    assert body == {
        "run_id": str(run.id),
        "conversation_id": sent["conversation_id"],
        "status": "FAILED",
        "plan": run.plan,
        "trace": None,
        "error": {"code": "AGENT_BUSY"},
        "retry_after_s": 5,
    }


def test_latest_run_of_conversation_for_restore(client: TestClient, user_id: uuid.UUID) -> None:
    created = client.post("/api/chat/conversations", json={}, headers=_headers(user_id)).json()
    path = f"/api/chat/conversations/{created['conversation_id']}/run"
    assert client.get(path, headers=_headers(user_id)).json() is None

    _, sent = _send(client, user_id, conversation_id=created["conversation_id"])

    assert client.get(path, headers=_headers(user_id)).json()["run_id"] == sent["run_id"]


def test_cancel_before_send_returns_the_service_reservation(
    client: TestClient, db_session: Session, user_id: uuid.UUID
) -> None:
    _, sent = _send(client, user_id)
    day = datetime.now(UTC).astimezone(SEOUL).date()
    reserved = select(ChatDailyUsage.reserved).where(
        ChatDailyUsage.user_id.is_(None), ChatDailyUsage.day == day
    )
    before = db_session.scalar(reserved)

    response = client.post(f"/api/chat/runs/{sent['run_id']}/cancel", headers=_headers(user_id))

    assert response.json()["status"] == "CANCELLED"
    assert response.json()["error"] == {"code": "AGENT_CANCELLED"}
    db_session.expire_all()
    assert db_session.scalar(reserved) == before - 1
    run = db_session.get(ChatRun, uuid.UUID(sent["run_id"]))
    assert run.row_revision == 1
    assert db_session.scalar(select(ChatRunLog.status).where(ChatRunLog.run_id == run.id)) == (
        "CANCELLED"
    )


def test_cancel_after_send_keeps_the_reservation(
    client: TestClient, db_session: Session, user_id: uuid.UUID
) -> None:
    _, sent = _send(client, user_id)
    run = db_session.get(ChatRun, uuid.UUID(sent["run_id"]))
    run.status = "RUNNING"
    run.turn_calls = [
        {"turn_call": 1, "request_id": "r", "state_revision": None, "stage": "SENDING"}
    ]
    db_session.flush()
    day = datetime.now(UTC).astimezone(SEOUL).date()
    reserved = select(ChatDailyUsage.reserved).where(
        ChatDailyUsage.user_id.is_(None), ChatDailyUsage.day == day
    )
    before = db_session.scalar(reserved)

    client.post(f"/api/chat/runs/{run.id}/cancel", headers=_headers(user_id))

    db_session.expire_all()
    assert db_session.scalar(reserved) == before


def test_cancel_of_finished_run_keeps_the_result(
    client: TestClient, db_session: Session, user_id: uuid.UUID
) -> None:
    # 완료가 먼저 확정됐으면 취소하지 않고 그 상태를 돌려준다
    _, sent = _send(client, user_id)
    db_session.get(ChatRun, uuid.UUID(sent["run_id"])).status = "DONE"
    # 취소 API 가 rollback 하는 경로다. 준비 데이터가 함께 되돌아가지 않게 commit 한다
    db_session.commit()

    response = client.post(f"/api/chat/runs/{sent['run_id']}/cancel", headers=_headers(user_id))

    assert response.json()["status"] == "DONE"


def test_cancel_of_auto_goal_check_closes_the_check(
    client: TestClient, db_session: Session, user_id: uuid.UUID
) -> None:
    # 임대를 PENDING 으로 두면 5분 뒤 같은 리뷰로 실행을 다시 만들고 확인 횟수를 또 줄인다
    created = client.post("/api/chat/conversations", json={}, headers=_headers(user_id)).json()
    conversation_id = uuid.UUID(created["conversation_id"])
    now = datetime.now(UTC)
    run = ChatRun(
        conversation_id=conversation_id,
        user_id=user_id,
        first_trigger="TAKE_REVIEWED",
        status="QUEUED",
        input={"review_event": {}, "page": HOME},
        accepted_at=now,
        deadline_at=now + timedelta(seconds=120),
        fencing_token=1,
    )
    db_session.add(run)
    db_session.flush()
    lease = ChatLease(
        kind="GOAL_CHECK",
        conversation_id=conversation_id,
        goal_version=1,
        take_id=uuid.uuid4(),
        review_revision=1,
        status="PENDING",
        run_id=run.id,
        owner="sweep",
        lease_until=now + timedelta(minutes=5),
        fencing_token=1,
    )
    db_session.add(lease)
    db_session.flush()

    response = client.post(f"/api/chat/runs/{run.id}/cancel", headers=_headers(user_id))

    assert response.json()["status"] == "CANCELLED"
    db_session.expire_all()
    assert db_session.get(ChatLease, lease.id).status == "DONE"


def test_conversations_list_filters_by_pitch_and_shows_title(
    client: TestClient, db_session: Session, user_id: uuid.UUID
) -> None:
    pitch_id = _pitch(db_session, user_id, "졸업 발표")
    client.post("/api/chat/conversations", json={}, headers=_headers(user_id))
    in_pitch = client.post(
        "/api/chat/conversations", json={"pitch_id": str(pitch_id)}, headers=_headers(user_id)
    ).json()

    listed = client.get(
        f"/api/chat/conversations?pitch_id={pitch_id}", headers=_headers(user_id)
    ).json()["conversations"]

    assert [item["conversation_id"] for item in listed] == [in_pitch["conversation_id"]]
    assert listed[0]["scope_pitch_title"] == "졸업 발표"
    assert (
        len(
            client.get("/api/chat/conversations", headers=_headers(user_id)).json()["conversations"]
        )
        == 2
    )


def test_create_conversation_with_others_pitch_is_404(
    client: TestClient, db_session: Session, user_id: uuid.UUID
) -> None:
    their_pitch = _pitch(db_session, _other_user(db_session))
    response = client.post(
        "/api/chat/conversations", json={"pitch_id": str(their_pitch)}, headers=_headers(user_id)
    )

    assert response.status_code == 404


def test_scope_change_is_blocked_while_running(
    client: TestClient, db_session: Session, user_id: uuid.UUID
) -> None:
    pitch_id = _pitch(db_session, user_id)
    _, sent = _send(client, user_id)
    path = f"/api/chat/conversations/{sent['conversation_id']}/scope"

    db_session.commit()
    running = client.patch(path, json={"pitch_id": str(pitch_id)}, headers=_headers(user_id))
    assert running.json()["code"] == "AGENT_RUN_IN_PROGRESS"

    db_session.get(ChatRun, uuid.UUID(sent["run_id"])).status = "DONE"
    db_session.flush()
    changed = client.patch(path, json={"pitch_id": str(pitch_id)}, headers=_headers(user_id))
    assert changed.status_code == 200
    assert changed.json()["scope_pitch_id"] == str(pitch_id)
    back = client.patch(path, json={"pitch_id": None}, headers=_headers(user_id))
    assert back.json()["scope_pitch_id"] is None


def _answer(
    db: Session, conversation_id: uuid.UUID, user_id: uuid.UUID, seq: int, **fields: Any
) -> ChatMessage:
    message = ChatMessage(
        conversation_id=conversation_id,
        user_id=user_id,
        seq=seq,
        role="assistant",
        text="Take 5 의 3장 시선 비율이 낮아요.",
        status="DONE",
        claims=[{"kind": "RECORD", "text": "Take 5 의 3장 시선 비율이 낮아요."}],
        evidence=[{"id": "e1"}],
        **fields,
    )
    db.add(message)
    db.flush()
    return message


def test_messages_are_paged_from_the_newest(
    client: TestClient, db_session: Session, user_id: uuid.UUID
) -> None:
    _, sent = _send(client, user_id)
    conversation_id = uuid.UUID(sent["conversation_id"])
    for seq in range(2, 6):
        _answer(db_session, conversation_id, user_id, seq)
    path = f"/api/chat/conversations/{conversation_id}/messages"

    page = client.get(f"{path}?limit=2", headers=_headers(user_id)).json()
    assert [message["seq"] for message in page["messages"]] == [4, 5]
    assert page["next_before_seq"] == 4

    rest = client.get(f"{path}?limit=10&before_seq=4", headers=_headers(user_id)).json()
    assert [message["seq"] for message in rest["messages"]] == [1, 2, 3]
    assert rest["next_before_seq"] is None


def test_answer_on_a_deleted_take_is_masked_on_read(
    client: TestClient, db_session: Session, user_id: uuid.UUID
) -> None:
    _, sent = _send(client, user_id)
    conversation_id = uuid.UUID(sent["conversation_id"])
    pitch_id = _pitch(db_session, user_id)
    kept = _answer(db_session, conversation_id, user_id, 2, source_pitch_ids=[str(pitch_id)])
    gone = _answer(
        db_session,
        conversation_id,
        user_id,
        3,
        source_pitch_ids=[str(pitch_id)],
        source_take_ids=[str(uuid.uuid4())],
    )

    messages = {
        message["message_id"]: message
        for message in client.get(
            f"/api/chat/conversations/{conversation_id}/messages", headers=_headers(user_id)
        ).json()["messages"]
    }

    assert messages[str(kept.id)]["masked"] is False
    assert messages[str(kept.id)]["evidence"] == [{"id": "e1"}]
    masked = messages[str(gone.id)]
    assert masked["masked"] is True
    assert masked["text"] == MASKED_TEXT
    assert (masked["claims"], masked["evidence"], masked["basis"]) == (None, None, None)


def test_masking_reads_the_stored_id_and_checks_the_owner(
    client: TestClient, db_session: Session, user_id: uuid.UUID
) -> None:
    # AI 기록 조립(request.py _current_sources)과 같은 기준이다. 대문자로 적힌 내 피치는 보이고,
    # 남의 피치를 근거로 삼은 답은 가린다
    _, sent = _send(client, user_id)
    conversation_id = uuid.UUID(sent["conversation_id"])
    mine = _pitch(db_session, user_id)
    theirs = _pitch(db_session, _other_user(db_session))
    upper = _answer(db_session, conversation_id, user_id, 2, source_pitch_ids=[str(mine).upper()])
    other = _answer(db_session, conversation_id, user_id, 3, source_pitch_ids=[str(theirs)])

    masked = {
        message["message_id"]: message["masked"]
        for message in client.get(
            f"/api/chat/conversations/{conversation_id}/messages", headers=_headers(user_id)
        ).json()["messages"]
    }

    assert (masked[str(upper.id)], masked[str(other.id)]) == (False, True)


def test_run_status_hides_progress_once_a_read_source_is_gone(
    client: TestClient, db_session: Session, user_id: uuid.UUID
) -> None:
    # 정리 작업이 1시간마다 비우기 전에도 지워진 근거의 진행은 보이지 않는다
    _, sent = _send(client, user_id)
    run = db_session.get(ChatRun, uuid.UUID(sent["run_id"]))
    pitch_id = _pitch(db_session, user_id)
    run.plan = [{"step": "기록 확인", "status": "DONE", "note": None}]
    run.trace = [{"text": "Take 5 를 읽었어요."}]
    run.read_sources = {"pitches": [str(pitch_id)], "takes": []}
    db_session.flush()
    path = f"/api/chat/runs/{run.id}"

    shown = client.get(path, headers=_headers(user_id)).json()
    assert (shown["plan"], shown["trace"]) == (run.plan, run.trace)

    run.read_sources = {"pitches": [str(pitch_id)], "takes": [str(uuid.uuid4())]}
    db_session.flush()
    hidden = client.get(path, headers=_headers(user_id)).json()
    assert (hidden["plan"], hidden["trace"]) == (None, None)


def _judgement(assessment_id: str, *supersedes: str) -> dict[str, Any]:
    return {
        "assessment_id": assessment_id,
        "verdict": "CONTRADICTED",
        "supersedes": list(supersedes),
    }


def test_each_judgement_shows_the_newest_judgement_that_replaced_it(
    client: TestClient, db_session: Session, user_id: uuid.UUID
) -> None:
    _, sent = _send(client, user_id)
    conversation_id = uuid.UUID(sent["conversation_id"])
    _answer(db_session, conversation_id, user_id, 2, assessments=[_judgement("a1")])
    _answer(db_session, conversation_id, user_id, 3, assessments=[_judgement("a2", "a1")])
    # 이 대화에 없는 판단 · 같은 메시지 안의 판단은 잇지 않는다
    _answer(
        db_session,
        conversation_id,
        user_id,
        4,
        assessments=[_judgement("a3", "other"), _judgement("a4", "a3")],
    )
    _answer(db_session, conversation_id, user_id, 5, assessments=[_judgement("a5", "a1")])
    path = f"/api/chat/conversations/{conversation_id}/messages"

    # 앞 판단만 담은 페이지에서도 대화 전체로 계산한 값이 같게 나온다
    oldest = client.get(f"{path}?limit=1&before_seq=3", headers=_headers(user_id)).json()
    assert oldest["messages"][0]["assessments"][0]["superseded_by"] == "a5"

    by_id = {
        item["assessment_id"]: item["superseded_by"]
        for message in client.get(path, headers=_headers(user_id)).json()["messages"]
        for item in message["assessments"] or []
    }
    assert by_id == {"a1": "a5", "a2": None, "a3": None, "a4": None, "a5": None}


@pytest.mark.parametrize("enabled", [True, False])
def test_goal_sweep_runs_only_while_chat_is_on(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, enabled: bool
) -> None:
    # 꺼 두면 자동 목표 확인이 AI 를 부르지 않는다. 다른 주기 작업은 client 픽스처가 막아 두었다
    from pitch_coach_backend import main
    from pitch_coach_backend.core.config import settings

    started: list[bool] = []

    def start() -> Any:
        started.append(True)
        return _no_sweep_stop

    monkeypatch.setattr(main, "start_goal_sweep", start)
    monkeypatch.setattr(settings, "agent_enabled", enabled)

    async def run_app() -> None:
        async with main.lifespan(main.app):
            pass

    asyncio.run(run_app())

    assert started == ([True] if enabled else [])
