"""대화 만들기 · 목록 · 범위 바꾸기 · 메시지 목록(chat-agent.md 12-3 대화 API)."""

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Row
from sqlalchemy.orm import Session

from pitch_coach_backend.module.chat.api_dto import (
    RESPONSE_MAX_BYTES,
    ChatMessageResponse,
    ConversationListItem,
    ConversationListResponse,
    ConversationResponse,
    MessageListResponse,
)
from pitch_coach_backend.module.chat.api_repository import ChatApiRepository
from pitch_coach_backend.module.chat.entity import ChatConversation, ChatMessage
from pitch_coach_backend.module.chat.exception import ChatRunInProgress, ChatTargetNotFound
from pitch_coach_backend.module.chat.repository import ChatRepository
from pitch_coach_backend.module.chat.service.conversation_cursor import decode_cursor, encode_cursor
from pitch_coach_backend.module.chat.service.owned import parse_uuid
from pitch_coach_backend.module.chat.service.request import APP_STARTED_AT, current_sources
from pitch_coach_backend.module.chat.service.stale import (
    MASKED_TEXT,
    sources_of,
    stale_reasons,
    uses_deleted,
)
from pitch_coach_backend.module.pitch.repository import PitchRepository

CONVERSATIONS_LIMIT = 50


def create_conversation(
    db: Session, user_id: uuid.UUID, pitch_id: uuid.UUID | None, *, memory_off: bool
) -> ConversationResponse:
    pitch_title = _owned_pitch_title(db, user_id, pitch_id)
    # 제목은 첫 질문 앞부분이다. 첫 메시지를 받을 때 채운다
    conversation = ChatConversation(
        user_id=user_id, scope_pitch_id=pitch_id, title="", without_memory=memory_off
    )
    db.add(conversation)
    db.commit()
    return _conversation_view(conversation, pitch_title)


def list_conversations(
    db: Session, user_id: uuid.UUID, pitch_id: uuid.UUID | None, *, cursor: str | None
) -> ConversationListResponse:
    after = decode_cursor(cursor) if cursor is not None else None
    repo = ChatApiRepository(db)
    rows = repo.list_conversations(user_id, pitch_id, limit=CONVERSATIONS_LIMIT + 1, after=after)
    has_more = len(rows) > CONVERSATIONS_LIMIT
    # 더 읽은 한 행은 다음 쪽이 있는지만 알린다. '최신 아님' 계산에 넣지 않는다
    rows = rows[:CONVERSATIONS_LIMIT]
    answers = repo.latest_answers([row.ChatConversation.id for row in rows])
    goal_versions = {row.ChatConversation.id: row.ChatConversation.goal_version for row in rows}
    reasons = _stale_reasons_of(db, user_id, answers, goal_versions)
    stale = {answer.conversation_id: bool(reasons[answer.id]) for answer in answers}
    last = rows[-1].ChatConversation if has_more else None
    return ConversationListResponse(
        conversations=[
            ConversationListItem(
                **_conversation_view(row.ChatConversation, row.pitch_title).model_dump(),
                stale=stale.get(row.ChatConversation.id, False),
            )
            for row in rows
        ],
        next_cursor=encode_cursor(last.last_user_activity_at, last.id) if last else None,
    )


def change_scope(
    db: Session, user_id: uuid.UUID, conversation_id: uuid.UUID, pitch_id: uuid.UUID | None
) -> ConversationResponse:
    repo = ChatApiRepository(db)
    conversation = repo.get_owned_conversation(conversation_id, user_id, lock=True)
    if conversation is None:
        db.rollback()
        raise ChatTargetNotFound()
    if ChatRepository(db).has_running_run(conversation.id):
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
    conversation = repo.get_owned_conversation(conversation_id, user_id)
    if conversation is None:
        raise ChatTargetNotFound()
    if before_seq is None:
        ChatRepository(db).touch_conversation_opened(conversation_id, datetime.now(UTC))
        db.commit()
    messages = ChatRepository(db).list_recent_messages(
        conversation_id, before_seq=before_seq, limit=limit + 1
    )
    has_more = len(messages) > limit
    messages = messages[-limit:] if has_more else messages
    judged = repo.list_assessments(conversation_id, from_seq=messages[0].seq) if messages else []
    # 페이지 뒤 메시지의 판단도 대체 표시에 쓰므로 그 근거까지 함께 읽는다
    sourced = [*messages, *judged]
    current = owned_sources(
        db,
        user_id,
        {value for item in sourced for value in item.source_pitch_ids or []},
        {value for item in sourced for value in item.source_take_ids or []},
    )
    # 가려지는 메시지의 판단은 없는 것으로 친다. 화면에 ID 를 내보이지 않고,
    # 정리 작업이 그 판단을 비운 뒤와 같은 값이 나온다
    superseded = superseded_by(
        [row.assessments for row in judged if not _uses_gone_source(row, current)]
    )
    reasons = _stale_reasons_of(db, user_id, messages, {conversation_id: conversation.goal_version})
    views = [
        _message_view(message, current, superseded, reasons[message.id]) for message in messages
    ]
    # 가린 메시지는 가린 크기로 세야 하므로 응답 모양을 만든 뒤에 줄인다. 큰 칸을 다시 읽지 않는다
    kept = _newest_within_size(views)
    return MessageListResponse(
        messages=kept,
        next_before_seq=kept[0].seq if has_more or len(kept) < len(views) else None,
    )


def _newest_within_size(views: list[ChatMessageResponse]) -> list[ChatMessageResponse]:
    """최근 메시지부터 보내는 그대로의 크기를 세어, 한도를 넘는 앞쪽 메시지를 뺀다(12-3).

    최근 메시지 하나는 크기와 상관없이 싣는다. 안 그러면 커서가 나아가지 못한다.
    """
    total = 0
    for index in range(len(views) - 1, -1, -1):
        # 메시지 사이의 쉼표 몫까지 센다
        total += len(views[index].model_dump_json().encode("utf-8")) + 1
        if total > RESPONSE_MAX_BYTES and index < len(views) - 1:
            return views[index + 1 :]
    return views


def _stale_reasons_of(
    db: Session,
    user_id: uuid.UUID,
    messages: list[ChatMessage],
    goal_versions: dict[uuid.UUID, int],
) -> dict[uuid.UUID, list[str]]:
    """메시지마다 '최신 아님' 사유(6-8). AI 에 보낼 기록을 조립할 때와 같은 함수로 계산한다.

    근거의 지금 판 번호는 메시지가 가리키는 피치 · Take 만, 겹치지 않게 한 번씩 읽는다.
    """
    pitch_ids: set[str] = set()
    take_ids: set[str] = set()
    for message in messages:
        pitch_ids.update(str(value) for value in message.source_pitch_ids or [])
        take_ids.update(str(value) for value in message.source_take_ids or [])
        if isinstance(message.basis, dict):
            basis_pitches, basis_takes = sources_of(message.basis.get("pitches"))
            pitch_ids.update(basis_pitches)
            take_ids.update(basis_takes)
    if not pitch_ids and not take_ids and not any(message.basis for message in messages):
        return {message.id: [] for message in messages}
    chat_repo = ChatRepository(db)
    current = current_sources(db, chat_repo, user_id, pitch_ids, take_ids)
    corpus_version = chat_repo.latest_corpus_version(APP_STARTED_AT)
    reasons: dict[uuid.UUID, list[str]] = {}
    for message in messages:
        if _uses_gone_source(message, current):
            reasons[message.id] = ["SOURCE_DELETED"]
            continue
        reasons[message.id] = stale_reasons(
            message.basis,
            message.created_at,
            current,
            goal_versions[message.conversation_id],
            corpus_version,
        )
    return reasons


def superseded_by(judged: list[Any]) -> dict[str, str]:
    """앞 판단 ID → 그 판단을 `supersedes` 로 가리킨 가장 최근 판단 ID.

    새 판단은 앞 메시지의 판단만 대체한다. 이 대화에 없는 ID 나 같은 메시지 · 뒤 메시지의 ID 를
    가리키면 잇지 않는다(12-3 '판단 저장'). 앞 판단은 고치지 않고 읽을 때 이 값만 붙인다.
    """
    earlier: set[str] = set()
    links: dict[str, str] = {}
    for assessments in judged:
        ids: list[str] = []
        for assessment in assessments if isinstance(assessments, list) else []:
            new_id = assessment.get("assessment_id") if isinstance(assessment, dict) else None
            if not isinstance(new_id, str):
                continue
            ids.append(new_id)
            supersedes = assessment.get("supersedes")
            # 목록이 아니면(예: 문자열) 글자 단위로 돌아 엉뚱한 판단을 잇지 않게 건너뛴다
            for old_id in supersedes if isinstance(supersedes, list) else []:
                if old_id in earlier:
                    links[old_id] = new_id
        earlier.update(ids)
    return links


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
        memory_off=conversation.without_memory,
    )


def owned_sources(
    db: Session, user_id: uuid.UUID, pitch_ids: set[Any], take_ids: set[Any]
) -> dict[str, dict[str, bool]]:
    """근거로 삼은 피치 · Take 중 아직 있고 이 사용자의 것. 한 번에 읽는다.

    저장된 문자열을 그대로 키로 두고, 값만 UUID 로 읽어 소유권까지 본다. AI 에 보낼 기록을 조립할
    때(request.py `current_sources`)와 같은 기준이라 화면과 AI 가 같은 답을 가린다.
    그쪽과 달리 판 번호는 읽지 않아 1초 조회 경로에서도 쓴다.
    지워진 근거는 1시간 정리 작업이 가리지만, 그 사이에도 보이면 안 되므로
    읽을 때 다시 본다(12-2 ①).
    """
    pitches = _parsed(pitch_ids)
    takes = _parsed(take_ids)
    repo = ChatApiRepository(db)
    owned_pitches = repo.owned_pitch_ids(user_id, set(pitches.values()))
    owned_takes = repo.owned_take_ids(user_id, set(takes.values()))
    # uses_deleted 는 없는 키를 지워진 것으로 본다
    return {
        "pitches": {key: True for key, value in pitches.items() if value in owned_pitches},
        "takes": {key: True for key, value in takes.items() if value in owned_takes},
    }


def _parsed(values: set[Any]) -> dict[str, uuid.UUID]:
    # 읽을 수 없는 ID 는 있는 것으로 볼 수 없다. 키를 두지 않아 지워진 것으로 남는다
    return {str(value): parsed for value in values if (parsed := parse_uuid(value)) is not None}


def _uses_gone_source(item: ChatMessage | Row, current: dict[str, dict[str, Any]]) -> bool:
    """메시지의 근거 중 지워졌거나 남의 것이 있는지."""
    return uses_deleted(
        {str(value) for value in item.source_pitch_ids or []},
        {str(value) for value in item.source_take_ids or []},
        current,
    )


def _message_view(
    message: ChatMessage,
    current: dict[str, dict[str, Any]],
    superseded: dict[str, str],
    reasons: list[str],
) -> ChatMessageResponse:
    masked = _uses_gone_source(message, current)
    # 가릴 때는 본문만이 아니라 그 근거에서 나온 모든 반환 필드를 뺀다(12-2)
    visible = None if masked else message
    assessments = (
        _with_superseded_by(visible.assessments, superseded) if visible is not None else None
    )
    return ChatMessageResponse(
        message_id=message.id,
        seq=message.seq,
        role=message.role,
        text=MASKED_TEXT if masked else message.text,
        status=message.status,
        run_id=message.run_id,
        again_of_message_id=message.again_of_message_id,
        created_at=message.created_at,
        claims=visible and visible.claims,
        cards=visible and visible.cards,
        assessments=assessments,
        evidence=visible and visible.evidence,
        sources=visible and visible.sources,
        basis=visible and visible.basis,
        plan=visible and visible.plan,
        trace=visible and visible.trace,
        masked=masked,
        stale_reasons=reasons,
    )


def _with_superseded_by(assessments: Any, superseded: dict[str, str]) -> Any:
    """판단마다 `superseded_by`(대체한 새 판단 ID, 없으면 null)를 붙인다.

    저장된 값은 바꾸지 않는다.
    """
    if not isinstance(assessments, list):
        return assessments
    return [
        {**item, "superseded_by": superseded.get(item.get("assessment_id"))}
        if isinstance(item, dict)
        else item
        for item in assessments
    ]
