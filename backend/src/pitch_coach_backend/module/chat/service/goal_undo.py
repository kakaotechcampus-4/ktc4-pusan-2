"""대화 목표 되돌리기(chat-agent.md 12-3 '되돌리기', 실패 표 L2-28 · L3-29).

목표 저장(`SAVE_GOAL`) 카드의 [되돌리기]다. 지금 목표 판이 그 행동이 저장한 판일 때만 이전 내용을
새 판으로 되살린다. 되돌리기는 판을 늘리기만 한다. 그래서 되돌린 판은 언제나 저장한 판 + 1 이고,
같은 요청을 다시 보내면 그 판을 다시 읽어 처음 결과를 돌려줄 수 있다.
판의 내용을 비우는 것은 기준 Take 가 지워졌을 때의 정리 작업뿐이고 그때도 `base_take_id` 는 남는다.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.orm import Session

from pitch_coach_backend.module.chat.api_dto import GoalUndoneResponse
from pitch_coach_backend.module.chat.api_repository import ChatApiRepository
from pitch_coach_backend.module.chat.entity import (
    ChatAction,
    ChatConversation,
    ChatGoal,
    ChatMessage,
)
from pitch_coach_backend.module.chat.exception import ChatStateInvalid, ChatTargetNotFound
from pitch_coach_backend.module.chat.repository import ChatRepository
from pitch_coach_backend.module.chat.service.request import goal_view
from pitch_coach_backend.module.take.repository import TakeRepository

UNDONE_TEXT = "목표를 되돌렸어요."
BASE_DELETED = "목표의 기준 기록이 삭제되어 새 목표가 필요해요."


@dataclass(frozen=True)
class _Target:
    """되돌릴 행동과 그 행동이 저장한 판 · 그 앞 판."""

    action: ChatAction
    saved: ChatGoal
    previous: ChatGoal | None
    saved_base_gone: bool
    previous_base_gone: bool


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
    chat_repo = ChatRepository(db)
    target = _load_target(repo, chat_repo, user_id, run_id, action_id)
    conversation = repo.get_owned_conversation(target.action.conversation_id, user_id, lock=True)
    if conversation is None:
        raise ChatTargetNotFound()
    # 대화를 잠근 뒤 행동을 다시 읽는다. 같은 되돌리기가 동시에 오면 뒤에 온 쪽이 처음 결과를 본다
    db.refresh(target.action, with_for_update=True)
    if target.action.status != "SUCCEEDED":
        raise ChatTargetNotFound()
    if target.action.undone_at is not None:
        first = chat_repo.get_goal(conversation.id, target.saved.version + 1)
        view = _view(run_id, action_id, first)
        db.rollback()
        return view
    _check_undoable(target, conversation)
    restored = _apply(db, chat_repo, target, conversation, now)
    db.commit()
    return _view(run_id, action_id, restored)


def _load_target(
    repo: ChatApiRepository,
    chat_repo: ChatRepository,
    user_id: uuid.UUID,
    run_id: uuid.UUID,
    action_id: str,
) -> _Target:
    # 실행 행은 끝나고 7일 뒤 지워도 행동 행은 대화 보관 기간까지 남는다. 실행 행을 거치지 않는다
    action = chat_repo.get_action(run_id, action_id)
    if action is None or action.user_id != user_id or action.type != "SAVE_GOAL":
        raise ChatTargetNotFound()
    saved = repo.goal_saved_by(action.conversation_id, action.id)
    if saved is None:
        raise ChatTargetNotFound()
    previous = chat_repo.get_goal(action.conversation_id, saved.version - 1)
    # 기준 Take 가 지워지는 것과 겹치지 않게 피치 · Take 를 대화보다 먼저 잠근다(턴 실행기와 같은
    # 순서). 피치를 지우는 쪽은 피치를 잡은 뒤 연쇄 삭제로 대화를 잡아, 반대로 잠그면 서로 기다린다
    return _Target(
        action=action,
        saved=saved,
        previous=previous,
        saved_base_gone=_base_gone(chat_repo, saved),
        previous_base_gone=previous is not None and _base_gone(chat_repo, previous),
    )


def _check_undoable(target: _Target, conversation: ChatConversation) -> None:
    # 판 번호보다 먼저 본다. 정리 작업이 목표를 비활성화해 판이 바뀐 경우도 이 이유로 알린다
    if target.saved_base_gone:
        raise ChatStateInvalid(BASE_DELETED)
    # 실행 상태와 상관없이 지금 목표 판으로만 따진다. 실행이 끝났거나 정리됐어도 되돌릴 수 있다
    if conversation.goal_version != target.saved.version:
        raise ChatStateInvalid()
    # 앞 판의 내용보다 먼저 본다. 정리 작업이 내용을 비운 뒤에도 정리 전과 같이 거절한다
    if target.previous_base_gone:
        raise ChatStateInvalid(BASE_DELETED)


def _apply(
    db: Session,
    chat_repo: ChatRepository,
    target: _Target,
    conversation: ChatConversation,
    now: datetime,
) -> ChatGoal:
    restored = _restored(db, conversation.id, target.saved.version, target.previous)
    db.add(restored)
    conversation.goal_version = restored.version
    target.action.undone_at = now
    db.add(
        ChatMessage(
            conversation_id=conversation.id,
            user_id=conversation.user_id,
            seq=chat_repo.next_message_seq(conversation.id),
            role="system",
            text=UNDONE_TEXT,
        )
    )
    return restored


def _base_gone(chat_repo: ChatRepository, goal: ChatGoal) -> bool:
    """목표의 기준 Take 가 지워졌는지. 있으면 Take · 피치 행을 잠가 지우기와 겹치지 않게 한다."""
    return goal.base_take_id is not None and chat_repo.lock_take_id(goal.base_take_id) is None


def _restored(
    db: Session, conversation_id: uuid.UUID, saved_version: int, previous: ChatGoal | None
) -> ChatGoal:
    """저장한 판 바로 앞 판의 내용을 새 판으로. 앞 판이 없거나 비었으면 내용 없는 판이다."""
    restored = ChatGoal(conversation_id=conversation_id, version=saved_version + 1)
    if previous is None or previous.goal is None:
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


def _view(run_id: uuid.UUID, action_id: str, restored: ChatGoal) -> GoalUndoneResponse:
    return GoalUndoneResponse(
        run_id=run_id,
        action_id=action_id,
        goal_version=restored.version,
        goal=goal_view(restored),
    )
