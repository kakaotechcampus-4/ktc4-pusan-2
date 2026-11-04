"""대화 목표 되돌리기(chat-agent.md 12-3 '되돌리기', 실패 표 L2-28 · L3-29).

목표 저장(`SAVE_GOAL`) 카드의 [되돌리기]다. 지금 목표 판이 그 행동이 저장한 판일 때만 이전 내용을
새 판으로 되살린다. 되돌리기는 판을 늘리기만 한다. 그래서 되돌린 판은 언제나 저장한 판 + 1 이고,
같은 요청을 다시 보내면 그 판을 다시 읽어 처음 결과를 돌려줄 수 있다.
판의 내용을 비우는 것은 기준 Take 가 지워졌을 때의 정리 작업뿐이고 그때도 `base_take_id` 는 남는다.
"""

import uuid
from datetime import datetime

from sqlalchemy.orm import Session

from pitch_coach_backend.module.chat.api_dto import GoalUndoneResponse
from pitch_coach_backend.module.chat.api_repository import ChatApiRepository
from pitch_coach_backend.module.chat.entity import ChatGoal, ChatMessage
from pitch_coach_backend.module.chat.exception import ChatStateInvalid, ChatTargetNotFound
from pitch_coach_backend.module.chat.repository import ChatRepository
from pitch_coach_backend.module.chat.service.request import goal_view
from pitch_coach_backend.module.take.repository import TakeRepository

UNDONE_TEXT = "목표를 되돌렸어요."
BASE_DELETED = "목표의 기준 기록이 삭제되어 새 목표가 필요해요."


def undo_goal(
    db: Session, user_id: uuid.UUID, run_id: uuid.UUID, action_id: str, now: datetime
) -> GoalUndoneResponse:
    try:
        return _undo(db, user_id, run_id, action_id, now)
    except Exception:
        db.rollback()
        raise


def _undo(
    db: Session, user_id: uuid.UUID, run_id: uuid.UUID, action_id: str, now: datetime
) -> GoalUndoneResponse:
    repo = ChatApiRepository(db)
    action = repo.get_owned_action(run_id, action_id, user_id)
    if action is None or action.type != "SAVE_GOAL":
        raise ChatTargetNotFound()
    conversation = repo.get_owned_conversation(action.conversation_id, user_id, lock=True)
    if conversation is None:
        raise ChatTargetNotFound()
    # 대화를 잠근 뒤 행동을 다시 읽는다. 같은 되돌리기가 동시에 오면 뒤에 온 쪽이 처음 결과를 본다
    db.refresh(action, with_for_update=True)
    saved = repo.goal_saved_by(action.id)
    if action.status != "SUCCEEDED" or saved is None:
        raise ChatTargetNotFound()
    chat_repo = ChatRepository(db)
    restored_version = saved.version + 1
    if action.undone_at is not None:
        view = _view(run_id, action_id, chat_repo.get_goal(conversation.id, restored_version))
        db.rollback()
        return view
    # 기준 Take 가 지워진 목표는 되돌리지 않는다. 지우기와 겹치지 않게 Take · 피치 행을 잠가 본다.
    # 판 번호보다 먼저 본다. 정리 작업이 목표를 비활성화해 판이 바뀐 경우도 이 이유로 알린다
    if saved.base_take_id is not None and chat_repo.lock_take_id(saved.base_take_id) is None:
        raise ChatStateInvalid(BASE_DELETED)
    # 실행 상태와 상관없이 지금 목표 판으로만 따진다. 실행이 끝났거나 정리됐어도 되돌릴 수 있다
    if conversation.goal_version != saved.version:
        raise ChatStateInvalid()
    restored = _restored(db, chat_repo, conversation.id, saved.version)
    db.add(restored)
    conversation.goal_version = restored_version
    action.undone_at = now
    db.add(
        ChatMessage(
            conversation_id=conversation.id,
            user_id=user_id,
            seq=chat_repo.next_message_seq(conversation.id),
            role="system",
            text=UNDONE_TEXT,
        )
    )
    db.commit()
    return _view(run_id, action_id, restored)


def _restored(
    db: Session, chat_repo: ChatRepository, conversation_id: uuid.UUID, saved_version: int
) -> ChatGoal:
    """저장한 판 바로 앞 판의 내용을 새 판으로. 앞 판이 없거나 비었으면 내용 없는 판이다."""
    restored = ChatGoal(conversation_id=conversation_id, version=saved_version + 1)
    previous = chat_repo.get_goal(conversation_id, saved_version - 1)
    if previous is None:
        return restored
    # 내용보다 먼저 본다. 정리 작업이 내용을 비운 뒤에도 정리 전과 같이 거절하려는 것이다
    if previous.base_take_id is not None and chat_repo.lock_take_id(previous.base_take_id) is None:
        raise ChatStateInvalid(BASE_DELETED)
    if previous.goal is None:
        return restored
    restored.goal = previous.goal
    # 끝난 목표(ACHIEVED 등)는 그대로 둔다. 되살렸다고 자동 판정을 다시 열지 않는다(L4-14)
    restored.status = previous.status
    restored.base_take_id = previous.base_take_id
    # 자동 확인은 되살린 때부터 시작한 Take 만 본다.
    # 앞 판에서 이미 판정한 Take 를 다시 판정하지 않는다
    restored.first_take_number = TakeRepository(db).next_take_number(
        uuid.UUID(previous.goal["pitch_id"])
    )
    return restored


def _view(run_id: uuid.UUID, action_id: str, restored: ChatGoal | None) -> GoalUndoneResponse:
    if restored is None:
        raise ChatTargetNotFound()
    return GoalUndoneResponse(
        run_id=run_id,
        action_id=action_id,
        goal_version=restored.version,
        goal=goal_view(restored),
    )
