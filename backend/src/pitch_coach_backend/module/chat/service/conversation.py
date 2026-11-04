"""대화 만들기 · 목록 · 범위 바꾸기 · 메시지 목록(chat-agent.md 12-3 대화 API)."""

import uuid
from typing import Any

from sqlalchemy.orm import Session

from pitch_coach_backend.module.chat.api_dto import (
    ChatMessageResponse,
    ConversationListResponse,
    ConversationResponse,
    MessageListResponse,
)
from pitch_coach_backend.module.chat.api_repository import ChatApiRepository
from pitch_coach_backend.module.chat.entity import ChatConversation, ChatMessage
from pitch_coach_backend.module.chat.exception import ChatRunInProgress, ChatTargetNotFound
from pitch_coach_backend.module.chat.repository import ChatRepository
from pitch_coach_backend.module.chat.service.stale import MASKED_TEXT, uses_deleted
from pitch_coach_backend.module.pitch.repository import PitchRepository

CONVERSATIONS_LIMIT = 50


def create_conversation(
    db: Session, user_id: uuid.UUID, pitch_id: uuid.UUID | None
) -> ConversationResponse:
    pitch_title = _owned_pitch_title(db, user_id, pitch_id)
    # 제목은 첫 질문 앞부분이다. 첫 메시지를 받을 때 채운다
    conversation = ChatConversation(user_id=user_id, scope_pitch_id=pitch_id, title="")
    db.add(conversation)
    db.commit()
    return _conversation_view(conversation, pitch_title)


def list_conversations(
    db: Session, user_id: uuid.UUID, pitch_id: uuid.UUID | None
) -> ConversationListResponse:
    rows = ChatApiRepository(db).list_conversations(user_id, pitch_id, limit=CONVERSATIONS_LIMIT)
    return ConversationListResponse(
        conversations=[_conversation_view(row.ChatConversation, row.pitch_title) for row in rows]
    )


def change_scope(
    db: Session, user_id: uuid.UUID, conversation_id: uuid.UUID, pitch_id: uuid.UUID | None
) -> ConversationResponse:
    repo = ChatApiRepository(db)
    conversation = repo.get_owned_conversation(conversation_id, user_id, lock=True)
    if conversation is None:
        db.rollback()
        raise ChatTargetNotFound()
    if repo.has_active_run(conversation.id):
        # 실행 중에 범위를 바꾸면 그 실행이 어느 범위로 답했는지 알 수 없게 된다
        db.rollback()
        raise ChatRunInProgress()
    try:
        pitch_title = _owned_pitch_title(db, user_id, pitch_id)
    except ChatTargetNotFound:
        db.rollback()
        raise
    # 대화 목표는 다른 피치로 옮기지 않는다. 목표 확인은 목표의 피치로 계속 돈다
    conversation.scope_pitch_id = pitch_id
    db.commit()
    return _conversation_view(conversation, pitch_title)


def list_messages(
    db: Session,
    user_id: uuid.UUID,
    conversation_id: uuid.UUID,
    *,
    before_seq: int | None,
    limit: int,
) -> MessageListResponse:
    repo = ChatApiRepository(db)
    if repo.get_owned_conversation(conversation_id, user_id) is None:
        raise ChatTargetNotFound()
    messages = repo.list_messages(conversation_id, before_seq=before_seq, limit=limit + 1)
    has_more = len(messages) > limit
    messages = messages[-limit:] if has_more else messages
    current = _existing_sources(db, messages)
    return MessageListResponse(
        messages=[_message_view(message, current) for message in messages],
        next_before_seq=messages[0].seq if has_more else None,
    )


def _owned_pitch_title(db: Session, user_id: uuid.UUID, pitch_id: uuid.UUID | None) -> str | None:
    if pitch_id is None:
        return None
    pitch = PitchRepository(db).get_owned(pitch_id, user_id)
    if pitch is None:
        raise ChatTargetNotFound()
    return pitch.title


def _conversation_view(
    conversation: ChatConversation, pitch_title: str | None
) -> ConversationResponse:
    return ConversationResponse(
        conversation_id=conversation.id,
        title=conversation.title,
        scope_pitch_id=conversation.scope_pitch_id,
        scope_pitch_title=pitch_title,
        last_user_activity_at=conversation.last_user_activity_at,
        created_at=conversation.created_at,
    )


def _existing_sources(db: Session, messages: list[ChatMessage]) -> dict[str, dict[str, Any]]:
    """한 페이지의 메시지가 근거로 삼은 피치 · Take 중 아직 있는 것. 한 번에 읽는다.

    지워진 근거는 1시간 정리 작업이 가리지만, 그 사이에도 보이면 안 되므로
    읽을 때 다시 본다(12-2 ①).
    """
    pitch_ids = {value for message in messages for value in message.source_pitch_ids or []}
    take_ids = {value for message in messages for value in message.source_take_ids or []}
    repo = ChatRepository(db)
    pitches = {str(value) for value in repo.existing_pitch_ids(_uuids(pitch_ids))}
    takes = {str(value) for value in repo.existing_take_ids(_uuids(take_ids))}
    # uses_deleted 는 값이 None 인 항목을 지워진 것으로 본다
    return {
        "pitches": {value: True for value in pitches},
        "takes": {value: True for value in takes},
    }


def _uuids(values: set[str]) -> set[uuid.UUID]:
    parsed: set[uuid.UUID] = set()
    for value in values:
        try:
            parsed.add(uuid.UUID(str(value)))
        except ValueError:
            # 읽을 수 없는 ID 는 있는 것으로 볼 수 없다. 조회하지 않아 지워진 것으로 남는다
            continue
    return parsed


def _message_view(message: ChatMessage, current: dict[str, dict[str, Any]]) -> ChatMessageResponse:
    pitch_ids = {str(value) for value in message.source_pitch_ids or []}
    take_ids = {str(value) for value in message.source_take_ids or []}
    masked = uses_deleted(pitch_ids, take_ids, current)
    # 가릴 때는 본문만이 아니라 그 근거에서 나온 모든 반환 필드를 뺀다(12-2)
    hidden = None if masked else message
    return ChatMessageResponse(
        message_id=message.id,
        seq=message.seq,
        role=message.role,
        text=MASKED_TEXT if masked else message.text,
        status=message.status,
        run_id=message.run_id,
        again_of_message_id=message.again_of_message_id,
        created_at=message.created_at,
        claims=hidden and hidden.claims,
        cards=hidden and hidden.cards,
        assessments=hidden and hidden.assessments,
        evidence=hidden and hidden.evidence,
        sources=hidden and hidden.sources,
        basis=hidden and hidden.basis,
        plan=hidden and hidden.plan,
        trace=hidden and hidden.trace,
        masked=masked,
    )
