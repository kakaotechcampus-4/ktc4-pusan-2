"""코칭 프로필 기억 설정 · 항목 API(chat-agent.md 6-14, 12-3 '기억 설정 · 코칭 프로필')."""

import time
import uuid
from collections.abc import Callable, Generator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, delete, select, text
from sqlalchemy.orm import Session

from pitch_coach_backend.module.chat.api_repository import ChatApiRepository
from pitch_coach_backend.module.chat.entity import (
    ChatConversation,
    ChatMemorySetting,
    ChatProfileItem,
    ChatRun,
)
from pitch_coach_backend.module.chat.repository import ChatRepository
from pitch_coach_backend.module.chat.service.memory import new_memory_cards
from pitch_coach_backend.module.chat.service.memory_settings import delete_all_items, set_enabled
from pitch_coach_backend.module.chat.service.request import _memory
from pitch_coach_backend.module.user.entity import User
from tests.chat.api_helpers import bearer

pytestmark = pytest.mark.usefixtures("chat_app")

HOME = {"route": "home", "pitch_id": None, "take_id": None, "screen": None}
SPOKEN = "앞으로 짧게 설명해 줘"
CANDIDATE = [{"key": "TONE", "value": "짧게 설명", "quote": SPOKEN}]


def _item(db: Session, user_id: uuid.UUID, key: str = "TONE", value: str = "짧게") -> uuid.UUID:
    item_id = ChatRepository(db).upsert_profile_item(user_id, key, value)
    db.commit()
    return item_id


def _version(db: Session, user_id: uuid.UUID) -> int | None:
    return db.scalar(
        select(ChatMemorySetting.memory_version).where(ChatMemorySetting.user_id == user_id)
    )


def _stored_items(db: Session, user_id: uuid.UUID) -> list[str]:
    return list(db.scalars(select(ChatProfileItem.key).where(ChatProfileItem.user_id == user_id)))


def _running_turn(
    client: TestClient,
    db: Session,
    user_id: uuid.UUID,
    memory_version: int = 0,
    **conversation: Any,
) -> tuple[ChatRun, ChatConversation]:
    """사용자 말로 시작해 처음 요청을 보낸 실행. 그때 받은 기억 번호를 기록해 둔다."""
    created = client.post("/api/chat/conversations", json=conversation, headers=bearer(user_id))
    response = client.post(
        "/api/chat/messages",
        json={
            "conversation_id": created.json()["conversation_id"],
            "text": SPOKEN,
            "page": HOME,
            "client_request_id": str(uuid.uuid4()),
        },
        headers=bearer(user_id),
    )
    assert response.status_code == 202
    row = db.get(ChatRun, uuid.UUID(response.json()["run_id"]))
    row.status = "RUNNING"
    row.turn_calls = [{"turn_call": 1, "memory_version": memory_version}]
    db.commit()
    return row, db.get(ChatConversation, row.conversation_id)


def _saved(db: Session, row: ChatRun, conversation: ChatConversation) -> list[dict[str, Any]]:
    return new_memory_cards(ChatRepository(db), row, conversation, CANDIDATE, [])


def test_default_is_on_with_no_items_and_reading_makes_no_row(
    client: TestClient, db_session: Session, user_id: uuid.UUID
) -> None:
    response = client.get("/api/chat/memory", headers=bearer(user_id))

    assert response.status_code == 200
    assert response.json() == {"enabled": True, "items": []}
    assert _version(db_session, user_id) is None


def test_items_saved_without_setting_row_are_listed(
    client: TestClient, db_session: Session, user_id: uuid.UUID
) -> None:
    # 기본 켜짐으로 저장할 때는 설정 행을 만들지 않는다
    tone = _item(db_session, user_id, "TONE", "짧게")
    context = _item(db_session, user_id, "CONTEXT", "졸업 발표")

    assert client.get("/api/chat/memory", headers=bearer(user_id)).json() == {
        "enabled": True,
        "items": [
            {"item_id": str(context), "key": "CONTEXT", "value": "졸업 발표"},
            {"item_id": str(tone), "key": "TONE", "value": "짧게"},
        ],
    }


def test_version_moves_only_when_the_value_really_changes(
    client: TestClient, db_session: Session, user_id: uuid.UUID
) -> None:
    item_id = _item(db_session, user_id)
    expected_items = [{"item_id": str(item_id), "key": "TONE", "value": "짧게"}]

    def put(enabled: bool) -> dict[str, Any]:
        response = client.put(
            "/api/chat/memory", json={"enabled": enabled}, headers=bearer(user_id)
        )
        assert response.status_code == 200
        return response.json()

    # 기본값도 켜짐이라 바뀐 것이 없다
    assert put(True) == {"enabled": True, "items": expected_items}
    assert _version(db_session, user_id) == 0
    # 끄기만으로는 항목을 지우지 않는다
    assert put(False) == {"enabled": False, "items": expected_items}
    assert _version(db_session, user_id) == 1
    assert put(False)["enabled"] is False
    assert _version(db_session, user_id) == 1
    assert put(True)["enabled"] is True
    assert _version(db_session, user_id) == 2


def test_turning_off_for_the_first_time_makes_the_row(
    client: TestClient, db_session: Session, user_id: uuid.UUID
) -> None:
    response = client.put("/api/chat/memory", json={"enabled": False}, headers=bearer(user_id))

    assert response.json() == {"enabled": False, "items": []}
    assert _version(db_session, user_id) == 1


@pytest.mark.parametrize("body", [{}, {"enabled": "false"}, {"enabled": 0}, {"enabled": None}])
def test_setting_needs_a_real_bool(client: TestClient, user_id: uuid.UUID, body: Any) -> None:
    response = client.put("/api/chat/memory", json=body, headers=bearer(user_id))
    assert response.status_code == 422


def test_delete_all_really_deletes_and_bumps_once(
    client: TestClient, db_session: Session, user_id: uuid.UUID
) -> None:
    _item(db_session, user_id, "TONE")
    _item(db_session, user_id, "CONTEXT")

    response = client.delete("/api/chat/memory/items", headers=bearer(user_id))

    assert response.status_code == 200
    assert response.json() == {"enabled": True, "items": []}
    assert _stored_items(db_session, user_id) == []
    assert _version(db_session, user_id) == 1


def test_delete_all_with_nothing_keeps_the_version(
    client: TestClient, db_session: Session, user_id: uuid.UUID
) -> None:
    db_session.commit()

    response = client.delete("/api/chat/memory/items", headers=bearer(user_id))
    assert response.json() == {"enabled": True, "items": []}
    assert _version(db_session, user_id) is None

    client.put("/api/chat/memory", json={"enabled": False}, headers=bearer(user_id))
    response = client.delete("/api/chat/memory/items", headers=bearer(user_id))
    assert response.json() == {"enabled": False, "items": []}
    assert _version(db_session, user_id) == 1


def test_delete_one_really_deletes_and_keeps_the_others(
    client: TestClient, db_session: Session, user_id: uuid.UUID
) -> None:
    tone = _item(db_session, user_id, "TONE", "짧게")
    context = _item(db_session, user_id, "CONTEXT", "졸업 발표")

    response = client.delete(f"/api/chat/memory/items/{tone}", headers=bearer(user_id))

    assert response.status_code == 200
    assert response.json() == {
        "enabled": True,
        "items": [{"item_id": str(context), "key": "CONTEXT", "value": "졸업 발표"}],
    }
    assert _stored_items(db_session, user_id) == ["CONTEXT"]
    assert _version(db_session, user_id) == 1


def test_missing_or_others_item_is_the_same_404_and_changes_nothing(
    client: TestClient, db_session: Session, user_id: uuid.UUID
) -> None:
    other = User(email=f"{uuid.uuid4()}@example.com", name="다른 사용자")
    db_session.add(other)
    db_session.flush()
    theirs = _item(db_session, other.id)
    mine = _item(db_session, user_id)

    gone = client.delete(f"/api/chat/memory/items/{uuid.uuid4()}", headers=bearer(user_id))
    stolen = client.delete(f"/api/chat/memory/items/{theirs}", headers=bearer(user_id))

    assert gone.status_code == stolen.status_code == 404
    assert gone.json() == stolen.json()
    assert _stored_items(db_session, other.id) == ["TONE"]
    assert _stored_items(db_session, user_id) == ["TONE"]
    assert _version(db_session, user_id) is None
    assert _version(db_session, other.id) is None
    listed = client.get("/api/chat/memory", headers=bearer(user_id)).json()["items"]
    assert [item["item_id"] for item in listed] == [str(mine)]


def test_deleting_does_not_touch_the_setting_value(
    client: TestClient, db_session: Session, user_id: uuid.UUID
) -> None:
    _item(db_session, user_id)
    client.put("/api/chat/memory", json={"enabled": False}, headers=bearer(user_id))

    response = client.delete("/api/chat/memory/items", headers=bearer(user_id))

    assert response.json() == {"enabled": False, "items": []}
    assert _version(db_session, user_id) == 2


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/api/chat/memory"),
        ("PUT", "/api/chat/memory"),
        ("DELETE", "/api/chat/memory/items"),
        ("DELETE", f"/api/chat/memory/items/{uuid.uuid4()}"),
    ],
)
def test_memory_api_needs_login_and_the_switch(
    client: TestClient,
    user_id: uuid.UUID,
    monkeypatch: pytest.MonkeyPatch,
    method: str,
    path: str,
) -> None:
    from pitch_coach_backend.core.config import settings

    body = {"json": {"enabled": True}} if method == "PUT" else {}
    assert client.request(method, path, **body).status_code == 401
    monkeypatch.setattr(settings, "agent_enabled", False)
    response = client.request(method, path, headers=bearer(user_id), **body)
    assert (response.status_code, response.json()["code"]) == (503, "AGENT_DISABLED")


def test_memory_off_is_stored_and_shown_everywhere(
    client: TestClient, db_session: Session, user_id: uuid.UUID
) -> None:
    headers = bearer(user_id)

    off = client.post("/api/chat/conversations", json={"memory_off": True}, headers=headers)
    on = client.post("/api/chat/conversations", json={}, headers=headers)

    assert off.status_code == on.status_code == 201
    assert off.json()["memory_off"] is True
    assert on.json()["memory_off"] is False
    assert db_session.get(ChatConversation, uuid.UUID(off.json()["conversation_id"])).without_memory
    listed = client.get("/api/chat/conversations", headers=headers).json()["conversations"]
    assert {row["conversation_id"]: row["memory_off"] for row in listed} == {
        off.json()["conversation_id"]: True,
        on.json()["conversation_id"]: False,
    }
    # 범위를 바꿔도 그대로다
    scoped = client.patch(
        f"/api/chat/conversations/{off.json()['conversation_id']}/scope",
        json={"pitch_id": None},
        headers=headers,
    )
    assert scoped.json()["memory_off"] is True


def test_memory_off_needs_a_real_bool(client: TestClient, user_id: uuid.UUID) -> None:
    response = client.post(
        "/api/chat/conversations", json={"memory_off": "true"}, headers=bearer(user_id)
    )
    assert response.status_code == 422


def test_message_can_not_choose_memory_off(client: TestClient, user_id: uuid.UUID) -> None:
    response = client.post(
        "/api/chat/messages",
        json={
            "conversation_id": None,
            "text": SPOKEN,
            "page": HOME,
            "client_request_id": str(uuid.uuid4()),
            "memory_off": True,
        },
        headers=bearer(user_id),
    )
    assert response.status_code == 422


def test_memory_off_conversation_sends_disabled_memory_and_saves_nothing(
    client: TestClient, db_session: Session, user_id: uuid.UUID
) -> None:
    _item(db_session, user_id, "CONTEXT", "졸업 발표")
    row, conversation = _running_turn(client, db_session, user_id, memory_off=True)

    memory = _memory(ChatRepository(db_session), user_id, conversation)

    assert (memory.enabled, memory.profile) == (False, None)
    assert _saved(db_session, row, conversation) == []
    assert _stored_items(db_session, user_id) == ["CONTEXT"]


def test_ordinary_conversation_sends_the_profile_and_saves(
    client: TestClient, db_session: Session, user_id: uuid.UUID
) -> None:
    _item(db_session, user_id, "CONTEXT", "졸업 발표")
    row, conversation = _running_turn(client, db_session, user_id)

    memory = _memory(ChatRepository(db_session), user_id, conversation)

    assert memory.enabled is True
    assert [item.key for item in memory.profile] == ["CONTEXT"]
    assert [card["key"] for card in _saved(db_session, row, conversation)] == ["TONE"]


def test_turning_off_stops_the_profile_and_saving(
    client: TestClient, db_session: Session, user_id: uuid.UUID
) -> None:
    row, conversation = _running_turn(client, db_session, user_id)

    client.put("/api/chat/memory", json={"enabled": False}, headers=bearer(user_id))
    db_session.expire_all()

    memory = _memory(ChatRepository(db_session), user_id, conversation)
    assert (memory.enabled, memory.memory_version, memory.profile) == (False, 1, None)
    assert _saved(db_session, row, conversation) == []
    assert _stored_items(db_session, user_id) == []


def test_turning_back_on_during_a_run_does_not_revive_its_candidates(
    client: TestClient, db_session: Session, user_id: uuid.UUID
) -> None:
    row, conversation = _running_turn(client, db_session, user_id)

    client.put("/api/chat/memory", json={"enabled": False}, headers=bearer(user_id))
    client.put("/api/chat/memory", json={"enabled": True}, headers=bearer(user_id))
    db_session.expire_all()

    assert _saved(db_session, row, conversation) == []


@pytest.mark.parametrize("target", ["ALL", "ONE"])
def test_deleting_during_a_run_drops_its_later_candidates(
    client: TestClient, db_session: Session, user_id: uuid.UUID, target: str
) -> None:
    item_id = _item(db_session, user_id, "CONTEXT", "졸업 발표")
    row, conversation = _running_turn(client, db_session, user_id)

    path = "/api/chat/memory/items" + (f"/{item_id}" if target == "ONE" else "")
    assert client.delete(path, headers=bearer(user_id)).status_code == 200
    db_session.expire_all()

    assert _saved(db_session, row, conversation) == []
    assert _stored_items(db_session, user_id) == []


def test_failed_delete_does_not_stop_the_run_from_saving(
    client: TestClient, db_session: Session, user_id: uuid.UUID
) -> None:
    row, conversation = _running_turn(client, db_session, user_id)

    path = f"/api/chat/memory/items/{uuid.uuid4()}"
    assert client.delete(path, headers=bearer(user_id)).status_code == 404
    assert client.delete("/api/chat/memory/items", headers=bearer(user_id)).status_code == 200

    assert [card["key"] for card in _saved(db_session, row, conversation)] == ["TONE"]


# ── 실제 커넥션 두 개로 줄 세우기를 본다 ───────────────────────────


@pytest.fixture
def committed_turn(engine: Engine) -> Generator[tuple[uuid.UUID, uuid.UUID, uuid.UUID]]:
    """실행 중인 턴을 실제로 커밋해 둔다. (사용자, 대화, 실행)"""
    now = datetime.now(UTC)
    with Session(engine, expire_on_commit=False) as db:
        user = User(email=f"{uuid.uuid4()}@example.com", name="동시 실행 검사")
        db.add(user)
        db.flush()
        conversation = ChatConversation(user_id=user.id, title="")
        db.add(conversation)
        db.flush()
        run = ChatRun(
            conversation_id=conversation.id,
            user_id=user.id,
            first_trigger="USER_MESSAGE",
            status="RUNNING",
            input={"user_message": SPOKEN},
            turn_calls=[{"turn_call": 1, "memory_version": 0}],
            accepted_at=now,
            deadline_at=now + timedelta(minutes=2),
        )
        db.add(run)
        db.commit()
        ids = (user.id, conversation.id, run.id)
    try:
        yield ids
    finally:
        with Session(engine) as db:
            db.execute(delete(User).where(User.id == ids[0]))
            db.commit()


def _wait_until_blocked(engine: Engine, pending: Any) -> None:
    """다른 커넥션이 잠금을 기다릴 때까지 본다. 끝나 버렸으면 줄을 서지 않은 것이다."""
    query = text(
        "SELECT count(*) FROM pg_stat_activity "
        "WHERE datname = current_database() AND wait_event_type = 'Lock'"
    )
    deadline = time.monotonic() + 5
    with engine.connect() as watcher:
        while time.monotonic() < deadline:
            if watcher.execute(query).scalar() or pending.done():
                break
            # 한 트랜잭션 안에서는 pg_stat_activity 값이 그대로라 끊고 다시 읽는다
            watcher.rollback()
            time.sleep(0.02)
    assert not pending.done(), "저장의 커밋을 기다리지 않았다"


def _call_with_own_session(engine: Engine, fn: Callable[[Session], Any]) -> Any:
    with Session(engine, autoflush=False, expire_on_commit=False) as db:
        return fn(db)


@pytest.mark.parametrize("has_setting_row", [True, False])
def test_delete_all_waits_for_a_save_in_progress_and_removes_it(
    engine: Engine, committed_turn: tuple[uuid.UUID, uuid.UUID, uuid.UUID], has_setting_row: bool
) -> None:
    user, conversation_id, run_id = committed_turn
    if has_setting_row:
        with Session(engine) as db:
            db.add(ChatMemorySetting(user_id=user))
            db.commit()

    with Session(engine, autoflush=False) as saving, ThreadPoolExecutor(max_workers=1) as pool:
        repo = ChatRepository(saving)
        # 턴 저장처럼 실행 행을 잠근 채로 후보를 검사 · 저장하고 아직 커밋하지 않았다
        row = repo.lock_run(run_id, statuses=("RUNNING",))
        conversation = repo.get_conversation(conversation_id)
        assert new_memory_cards(repo, row, conversation, CANDIDATE, [])[0]["key"] == "TONE"

        pending = pool.submit(_call_with_own_session, engine, lambda db: delete_all_items(db, user))
        _wait_until_blocked(engine, pending)
        saving.commit()
        view = pending.result(timeout=5)

    assert view.items == []
    with Session(engine) as db:
        assert _stored_items(db, user) == []
        assert _version(db, user) == 1


def test_turning_off_waits_for_a_save_without_setting_row(
    engine: Engine, committed_turn: tuple[uuid.UUID, uuid.UUID, uuid.UUID]
) -> None:
    user, conversation_id, run_id = committed_turn

    with Session(engine, autoflush=False) as saving, ThreadPoolExecutor(max_workers=1) as pool:
        repo = ChatRepository(saving)
        row = repo.lock_run(run_id, statuses=("RUNNING",))
        new_memory_cards(repo, row, repo.get_conversation(conversation_id), CANDIDATE, [])

        pending = pool.submit(
            _call_with_own_session, engine, lambda db: set_enabled(db, user, False)
        )
        _wait_until_blocked(engine, pending)
        saving.commit()
        view = pending.result(timeout=5)

    # 끄기는 항목을 지우지 않는다. 먼저 저장된 항목이 응답에 보여 사용자가 지울 수 있다
    assert (view.enabled, [item.key for item in view.items]) == (False, ["TONE"])


def test_save_waits_for_a_delete_in_progress_and_gives_up(
    engine: Engine, committed_turn: tuple[uuid.UUID, uuid.UUID, uuid.UUID]
) -> None:
    user, conversation_id, run_id = committed_turn
    with Session(engine) as db:
        db.add(ChatProfileItem(user_id=user, key="CONTEXT", value="졸업 발표"))
        db.add(ChatMemorySetting(user_id=user))
        db.commit()

    def save(db: Session) -> list[dict[str, Any]]:
        repo = ChatRepository(db)
        row = repo.lock_run(run_id, statuses=("RUNNING",))
        cards = new_memory_cards(repo, row, repo.get_conversation(conversation_id), CANDIDATE, [])
        db.commit()
        return cards

    with Session(engine, autoflush=False) as deleting, ThreadPoolExecutor(max_workers=1) as pool:
        repo = ChatApiRepository(deleting)
        repo.bump_memory_version(user)
        repo.delete_profile_items(user)

        pending = pool.submit(_call_with_own_session, engine, save)
        _wait_until_blocked(engine, pending)
        deleting.commit()
        assert pending.result(timeout=5) == []

    with Session(engine) as db:
        assert _stored_items(db, user) == []
