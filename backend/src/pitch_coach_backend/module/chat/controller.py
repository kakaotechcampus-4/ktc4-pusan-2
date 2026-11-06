"""코치 대화 API(화면 ↔ BE). 동작 · 값은 docs/ai/features/chat-agent.md 12-3 '대화 API' 를 따른다.

모든 API 는 매 요청 인증 · 소유권을 검사한다. 없거나 남의 대상은 같은 404 다.
AI 를 부르는 쪽(턴 실행기)은 turn_runner.py 에 있고, 여기서는 메시지를 접수한 뒤 띄우기만 한다.
"""

import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, Path, Query, status
from sqlalchemy.orm import Session

from pitch_coach_backend.core.config import settings
from pitch_coach_backend.core.database import get_db
from pitch_coach_backend.module.auth.dependencies import CurrentUser
from pitch_coach_backend.module.chat import turn_runner
from pitch_coach_backend.module.chat.api_dto import (
    MESSAGES_PAGE_MAX,
    ActionDecisionRequest,
    AvailabilityResponse,
    ChangeScopeRequest,
    ConversationListResponse,
    ConversationResponse,
    CreateConversationRequest,
    GoalUndoneResponse,
    MessageAcceptedResponse,
    MessageListResponse,
    RunStatusResponse,
    SendMessageRequest,
)
from pitch_coach_backend.module.chat.exception import ChatDisabled
from pitch_coach_backend.module.chat.service.accept import accept_message
from pitch_coach_backend.module.chat.service.approval import decide_action
from pitch_coach_backend.module.chat.service.conversation import (
    change_scope,
    create_conversation,
    list_conversations,
    list_messages,
)
from pitch_coach_backend.module.chat.service.goal_undo import undo_goal
from pitch_coach_backend.module.chat.service.run_view import (
    cancel_run,
    latest_run_status,
    run_status,
)

router = APIRouter(prefix="/chat", tags=["Chat"])

DbSession = Annotated[Session, Depends(get_db)]


def _require_enabled() -> None:
    if not settings.agent_enabled:
        raise ChatDisabled()


Enabled = Depends(_require_enabled)


async def _start_turn(run_id: uuid.UUID) -> None:
    # 턴 실행기는 이벤트 루프에 작업을 건다. 응답을 보낸 뒤(커밋이 끝난 뒤) 루프에서 띄운다
    turn_runner.start(run_id)


async def _resume_turn(run_id: uuid.UUID) -> None:
    turn_runner.resume(run_id)


# 버튼을 보일지. 스위치 값만 본다. 꺼져 있어도 503 이 아니라 enabled:false 로 답한다
@router.get("/availability", response_model=AvailabilityResponse)
def get_availability(current_user: CurrentUser) -> AvailabilityResponse:
    return AvailabilityResponse(enabled=settings.agent_enabled)


@router.post(
    "/conversations",
    status_code=status.HTTP_201_CREATED,
    response_model=ConversationResponse,
    dependencies=[Enabled],
)
def post_conversation(
    body: CreateConversationRequest, current_user: CurrentUser, db: DbSession
) -> ConversationResponse:
    return create_conversation(db, current_user.id, body.pitch_id, memory_off=body.memory_off)


@router.get("/conversations", response_model=ConversationListResponse, dependencies=[Enabled])
def get_conversations(
    current_user: CurrentUser, db: DbSession, pitch_id: uuid.UUID | None = None
) -> ConversationListResponse:
    return list_conversations(db, current_user.id, pitch_id)


@router.patch(
    "/conversations/{conversation_id}/scope",
    response_model=ConversationResponse,
    dependencies=[Enabled],
)
def patch_scope(
    conversation_id: uuid.UUID,
    body: ChangeScopeRequest,
    current_user: CurrentUser,
    db: DbSession,
) -> ConversationResponse:
    return change_scope(db, current_user.id, conversation_id, body.pitch_id)


@router.get(
    "/conversations/{conversation_id}/messages",
    response_model=MessageListResponse,
    dependencies=[Enabled],
)
def get_messages(
    conversation_id: uuid.UUID,
    current_user: CurrentUser,
    db: DbSession,
    before_seq: Annotated[int | None, Query(ge=1)] = None,
    limit: Annotated[int, Query(ge=1, le=MESSAGES_PAGE_MAX)] = 30,
) -> MessageListResponse:
    return list_messages(db, current_user.id, conversation_id, before_seq=before_seq, limit=limit)


# 새로고침 뒤 복원용. 실행이 한 번도 없으면 null
@router.get(
    "/conversations/{conversation_id}/run",
    response_model=RunStatusResponse | None,
    dependencies=[Enabled],
)
def get_latest_run(
    conversation_id: uuid.UUID,
    current_user: CurrentUser,
    db: DbSession,
    after_seq: Annotated[int, Query(ge=0)] = 0,
) -> RunStatusResponse | None:
    return latest_run_status(db, current_user.id, conversation_id, after_seq)


# 202: 메시지 · 실행 · 하루 한도 차감을 커밋한 뒤 돌려준다. 답은 실행 상태 조회로 받는다
@router.post(
    "/messages",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=MessageAcceptedResponse,
    dependencies=[Enabled],
)
def post_message(
    body: SendMessageRequest,
    current_user: CurrentUser,
    db: DbSession,
    background_tasks: BackgroundTasks,
) -> MessageAcceptedResponse:
    accepted = accept_message(db, current_user.id, body, datetime.now(UTC))
    if accepted.start:
        background_tasks.add_task(_start_turn, accepted.run_id)
    return MessageAcceptedResponse(
        conversation_id=accepted.conversation_id,
        message_id=accepted.message_id,
        run_id=accepted.run_id,
    )


# FE 가 실행 중에는 1초마다, 그 밖에는 2초마다 부른다. 실패하면 간격을 늘린다
@router.get("/runs/{run_id}", response_model=RunStatusResponse, dependencies=[Enabled])
def get_run(
    run_id: uuid.UUID,
    current_user: CurrentUser,
    db: DbSession,
    after_seq: Annotated[int, Query(ge=0)] = 0,
) -> RunStatusResponse:
    return run_status(db, current_user.id, run_id, after_seq)


@router.post("/runs/{run_id}/cancel", response_model=RunStatusResponse, dependencies=[Enabled])
def post_cancel(run_id: uuid.UUID, current_user: CurrentUser, db: DbSession) -> RunStatusResponse:
    return cancel_run(db, current_user.id, run_id)


# 확인 카드의 [승인] · [거절]. 같은 요청은 처음 결과를 준다. 거절은 AI 를 부르지 않는다
@router.post(
    "/runs/{run_id}/actions/{action_id}/decision",
    response_model=RunStatusResponse,
    dependencies=[Enabled],
)
def post_decision(
    run_id: uuid.UUID,
    action_id: Annotated[str, Path(min_length=1, max_length=64)],
    body: ActionDecisionRequest,
    current_user: CurrentUser,
    db: DbSession,
    background_tasks: BackgroundTasks,
) -> RunStatusResponse:
    decided = decide_action(
        db, current_user.id, run_id, action_id, body.decision, body.args_hash, datetime.now(UTC)
    )
    if decided.resume:
        background_tasks.add_task(_resume_turn, run_id)
    return decided.view


# 목표 저장 카드의 [되돌리기]. 실행이 끝난 뒤에도 쓸 수 있다. 같은 요청은 처음 결과를 준다
@router.post(
    "/runs/{run_id}/actions/{action_id}/undo",
    response_model=GoalUndoneResponse,
    dependencies=[Enabled],
)
def post_undo(
    run_id: uuid.UUID,
    action_id: Annotated[str, Path(min_length=1, max_length=64)],
    current_user: CurrentUser,
    db: DbSession,
) -> GoalUndoneResponse:
    return undo_goal(db, current_user.id, run_id, action_id, datetime.now(UTC))
