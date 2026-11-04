"""대화 목표 되돌리기(chat-agent.md 12-3 '되돌리기', L2-28 · L3-29)."""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import null, select, update
from sqlalchemy.orm import Session

from pitch_coach_backend.core.security import create_access_token
from pitch_coach_backend.module.chat.api_repository import ChatApiRepository
from pitch_coach_backend.module.chat.entity import (
    ChatAction,
    ChatConversation,
    ChatGoal,
    ChatMessage,
    ChatRun,
)
from pitch_coach_backend.module.chat.repository import ChatRepository
from pitch_coach_backend.module.chat.service.goal_undo import BASE_DELETED, UNDONE_TEXT
from pitch_coach_backend.module.pitch.entity import Pitch, PresentationVersion, ScriptVersion
from pitch_coach_backend.module.take.entity import Take
from pitch_coach_backend.module.user.entity import User


async def _no_sweep_stop() -> None:
    return None


def _no_sweep() -> Any:
    return _no_sweep_stop


@pytest.fixture(autouse=True)
def chat_app(monkeypatch: pytest.MonkeyPatch) -> None:
    """대화를 켜고, 턴 실행기 · 대화 주기 작업이 실제 DB · AI 를 부르지 않게 막는다."""
    from pitch_coach_backend import main
    from pitch_coach_backend.core.config import settings
    from pitch_coach_backend.module.chat import controller

    monkeypatch.setattr(settings, "agent_enabled", True)
    monkeypatch.setattr(main, "start_goal_sweep", _no_sweep)
    monkeypatch.setattr(main, "start_cleanup_sweep", _no_sweep)
    monkeypatch.setattr(controller.turn_runner, "start", lambda run_id: None)


def _headers(user_id: uuid.UUID) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(user_id)}"}


def _pitch(db: Session, user_id: uuid.UUID) -> Pitch:
    pitch = Pitch(user_id=user_id, title="졸업 발표", time_limit_sec=300)
    db.add(pitch)
    db.flush()
    db.add_all(
        [
            PresentationVersion(pitch_id=pitch.id, version=1, file_key=f"{pitch.id}/1.pdf"),
            ScriptVersion(pitch_id=pitch.id, version=1, content="대본"),
        ]
    )
    db.flush()
    return pitch


def _take(db: Session, pitch: Pitch, number: int) -> Take:
    presentation = db.scalars(select(PresentationVersion).filter_by(pitch_id=pitch.id)).one()
    script = db.scalars(select(ScriptVersion).filter_by(pitch_id=pitch.id)).one()
    take = Take(
        pitch_id=pitch.id,
        take_number=number,
        presentation_version_id=presentation.id,
        script_version_id=script.id,
        mode="COACHING",
        script_mode="HIGHLIGHT",
        goal_time_sec=300,
    )
    db.add(take)
    db.flush()
    return take


def _conversation(db: Session, user_id: uuid.UUID) -> ChatConversation:
    conversation = ChatConversation(user_id=user_id, title="목표 잡기")
    db.add(conversation)
    db.flush()
    return conversation


def _run(db: Session, conversation: ChatConversation) -> ChatRun:
    now = datetime.now(UTC)
    run = ChatRun(
        conversation_id=conversation.id,
        user_id=conversation.user_id,
        first_trigger="USER_MESSAGE",
        status="DONE",
        input={"user_message": "목표로 잡아 줘", "page": {"route": "home"}, "choice": None},
        accepted_at=now,
        deadline_at=now + timedelta(seconds=120),
    )
    db.add(run)
    db.flush()
    return run


def _save(
    db: Session,
    conversation: ChatConversation,
    run: ChatRun,
    take: Take,
    action_id: str,
    metric: str = "audience_ratio",
) -> ChatAction:
    """목표 저장 행동이 성공해 새 목표 판을 만든 상태(#903 의 save_goal 과 같은 모양)."""
    action = ChatAction(
        conversation_id=conversation.id,
        user_id=conversation.user_id,
        run_id=run.id,
        action_id=action_id,
        type="SAVE_GOAL",
        args={},
        args_digest="digest",
        autonomy="ASK",
        idempotency_key=f"key-{action_id}",
        status="SUCCEEDED",
    )
    db.add(action)
    db.flush()
    version = conversation.goal_version + 1
    goal = {
        "pitch_id": str(take.pitch_id),
        "metric": metric,
        "slide_number": 3,
        "base": {"take_id": str(take.id), "take_number": take.take_number},
    }
    db.add(
        ChatGoal(
            conversation_id=conversation.id,
            version=version,
            goal=goal,
            status="OPEN",
            base_take_id=take.id,
            first_take_number=take.take_number + 1,
            action_id=action.id,
        )
    )
    conversation.goal_version = version
    db.flush()
    return action


def _undo(client: TestClient, user_id: uuid.UUID, run_id: uuid.UUID, action_id: str) -> Any:
    return client.post(
        f"/api/chat/runs/{run_id}/actions/{action_id}/undo", headers=_headers(user_id)
    )


def _system_texts(db: Session, conversation_id: uuid.UUID) -> list[str]:
    return list(
        db.scalars(
            select(ChatMessage.text).where(
                ChatMessage.conversation_id == conversation_id, ChatMessage.role == "system"
            )
        )
    )


@pytest.fixture
def saved(db_session: Session, user_id: uuid.UUID) -> dict[str, Any]:
    pitch = _pitch(db_session, user_id)
    take = _take(db_session, pitch, 1)
    conversation = _conversation(db_session, user_id)
    run = _run(db_session, conversation)
    action = _save(db_session, conversation, run, take, "a-1")
    # 되돌리기가 실패하면 rollback 한다. 준비 데이터가 함께 되돌아가지 않게 commit 한다
    db_session.commit()
    return {
        "pitch": pitch,
        "take": take,
        "conversation": conversation,
        "run": run,
        "action": action,
    }


def test_undoing_the_first_save_leaves_an_empty_goal_and_a_system_message(
    client: TestClient, db_session: Session, user_id: uuid.UUID, saved: dict[str, Any]
) -> None:
    conversation, run, action = saved["conversation"], saved["run"], saved["action"]

    response = _undo(client, user_id, run.id, "a-1")

    assert response.status_code == 200
    assert response.json() == {
        "run_id": str(run.id),
        "action_id": "a-1",
        "goal_version": 2,
        "goal": None,
    }
    db_session.expire_all()
    assert db_session.get(ChatConversation, conversation.id).goal_version == 2
    empty = db_session.get(ChatGoal, (conversation.id, 2))
    assert (empty.goal, empty.status, empty.action_id) == (None, None, None)
    assert db_session.get(ChatAction, action.id).undone_at is not None
    assert _system_texts(db_session, conversation.id) == [UNDONE_TEXT]


def test_undo_restores_the_previous_goal_as_a_new_version(
    client: TestClient, db_session: Session, user_id: uuid.UUID, saved: dict[str, Any]
) -> None:
    conversation, run = saved["conversation"], saved["run"]
    second_take = _take(db_session, saved["pitch"], 2)
    _save(db_session, conversation, run, second_take, "a-2", metric="script_dependency")
    db_session.commit()

    response = _undo(client, user_id, run.id, "a-2")

    body = response.json()
    assert (body["goal_version"], body["goal"]["metric"], body["goal"]["version"]) == (
        3,
        "audience_ratio",
        3,
    )
    db_session.expire_all()
    first = db_session.get(ChatGoal, (conversation.id, 1))
    restored = db_session.get(ChatGoal, (conversation.id, 3))
    assert restored.goal == first.goal
    assert (restored.status, restored.base_take_id, restored.action_id) == (
        "OPEN",
        saved["take"].id,
        None,
    )
    # 자동 확인은 되살린 때부터 시작한 Take 만 본다. Take 1 · 2 가 있으니 3번부터다
    assert restored.first_take_number == 3


def test_undo_after_another_goal_was_saved_is_409(
    client: TestClient, db_session: Session, user_id: uuid.UUID, saved: dict[str, Any]
) -> None:
    second_take = _take(db_session, saved["pitch"], 2)
    _save(db_session, saved["conversation"], saved["run"], second_take, "a-2")
    db_session.commit()

    response = _undo(client, user_id, saved["run"].id, "a-1")

    assert response.status_code == 409
    assert response.json()["code"] == "AGENT_STATE_INVALID"
    assert response.json()["message"] == "이미 다른 목표로 바뀌었어요."


def test_same_undo_again_returns_the_first_result_once(
    client: TestClient, db_session: Session, user_id: uuid.UUID, saved: dict[str, Any]
) -> None:
    conversation, run = saved["conversation"], saved["run"]
    first = _undo(client, user_id, run.id, "a-1").json()
    # 처음 되돌린 뒤 다른 목표가 저장돼도 같은 요청에는 처음 결과를 준다
    second_take = _take(db_session, saved["pitch"], 2)
    _save(db_session, db_session.get(ChatConversation, conversation.id), run, second_take, "a-2")
    db_session.commit()

    again = _undo(client, user_id, run.id, "a-1")

    assert again.status_code == 200
    assert again.json() == first
    db_session.expire_all()
    assert _system_texts(db_session, conversation.id) == [UNDONE_TEXT]


def test_undo_still_works_after_the_run_is_cleaned_up(
    client: TestClient, db_session: Session, user_id: uuid.UUID, saved: dict[str, Any]
) -> None:
    # 실행 행은 끝나고 7일 뒤 지운다. 행동 행이 남아 있으면 되돌릴 수 있다
    db_session.delete(saved["run"])
    db_session.commit()

    response = _undo(client, user_id, saved["run"].id, "a-1")

    assert response.status_code == 200


def test_goal_whose_base_take_is_deleted_is_not_undone(
    client: TestClient, db_session: Session, user_id: uuid.UUID, saved: dict[str, Any]
) -> None:
    db_session.delete(saved["take"])
    db_session.commit()

    response = _undo(client, user_id, saved["run"].id, "a-1")

    assert response.status_code == 409
    assert response.json()["message"] == BASE_DELETED
    db_session.expire_all()
    assert db_session.get(ChatConversation, saved["conversation"].id).goal_version == 1


def test_previous_goal_whose_base_take_is_deleted_is_not_restored(
    client: TestClient, db_session: Session, user_id: uuid.UUID, saved: dict[str, Any]
) -> None:
    other_pitch = _pitch(db_session, user_id)
    other_take = _take(db_session, other_pitch, 1)
    _save(db_session, saved["conversation"], saved["run"], other_take, "a-2")
    db_session.delete(saved["take"])
    db_session.commit()

    response = _undo(client, user_id, saved["run"].id, "a-2")

    assert response.status_code == 409
    assert response.json()["message"] == BASE_DELETED


def test_previous_goal_emptied_by_cleanup_is_still_not_restored(
    client: TestClient, db_session: Session, user_id: uuid.UUID, saved: dict[str, Any]
) -> None:
    # 정리 작업이 앞 판을 비운 뒤에도 정리 전과 같다. 빈 판을 만들면 지금 목표만 사라진다
    other_pitch = _pitch(db_session, user_id)
    other_take = _take(db_session, other_pitch, 1)
    _save(db_session, saved["conversation"], saved["run"], other_take, "a-2")
    db_session.delete(saved["take"])
    # 정리 작업이 남기는 모양이다. 내용만 비우고 base_take_id 는 둔다
    db_session.execute(
        update(ChatGoal)
        .where(ChatGoal.conversation_id == saved["conversation"].id, ChatGoal.version == 1)
        .values(goal=null(), status=None)
    )
    db_session.commit()

    response = _undo(client, user_id, saved["run"].id, "a-2")

    assert response.status_code == 409
    assert response.json()["message"] == BASE_DELETED
    db_session.expire_all()
    assert db_session.get(ChatConversation, saved["conversation"].id).goal_version == 2


def test_pitch_and_take_are_locked_before_the_conversation(
    client: TestClient,
    db_session: Session,
    user_id: uuid.UUID,
    saved: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 피치를 지우는 쪽은 피치 → (연쇄 삭제) 대화 순서로 잠근다. 반대로 잠그면 서로 기다린다
    order: list[str] = []
    lock_take_id = ChatRepository.lock_take_id
    get_owned_conversation = ChatApiRepository.get_owned_conversation

    def recording_lock_take_id(self: ChatRepository, take_id: uuid.UUID) -> uuid.UUID | None:
        order.append("take")
        return lock_take_id(self, take_id)

    def recording_get_owned_conversation(
        self: ChatApiRepository, conversation_id: uuid.UUID, owner: uuid.UUID, *, lock: bool = False
    ) -> ChatConversation | None:
        if lock:
            order.append("conversation")
        return get_owned_conversation(self, conversation_id, owner, lock=lock)

    monkeypatch.setattr(ChatRepository, "lock_take_id", recording_lock_take_id)
    monkeypatch.setattr(
        ChatApiRepository, "get_owned_conversation", recording_get_owned_conversation
    )

    assert _undo(client, user_id, saved["run"].id, "a-1").status_code == 200

    assert order == ["take", "conversation"]


def test_only_my_goal_saves_can_be_undone(
    client: TestClient, db_session: Session, user_id: uuid.UUID, saved: dict[str, Any]
) -> None:
    other = User(email=f"{uuid.uuid4()}@example.com", name="다른 사용자")
    db_session.add(other)
    fetch = ChatAction(
        conversation_id=saved["conversation"].id,
        user_id=user_id,
        run_id=saved["run"].id,
        action_id="a-fetch",
        type="FETCH_TAKE",
        args={},
        args_digest="digest",
        autonomy="AUTO",
        idempotency_key="key-fetch",
        status="SUCCEEDED",
    )
    db_session.add(fetch)
    db_session.commit()
    run_id = saved["run"].id

    assert _undo(client, other.id, run_id, "a-1").status_code == 404
    assert _undo(client, user_id, run_id, "a-fetch").status_code == 404
    assert _undo(client, user_id, run_id, "a-none").status_code == 404
    assert _undo(client, user_id, uuid.uuid4(), "a-1").status_code == 404
