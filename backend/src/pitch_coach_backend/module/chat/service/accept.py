"""메시지 접수(chat-agent.md 12-3 '메시지 보내기').

메시지 · 실행 · 하루 한도 차감을 한 트랜잭션으로 커밋한 뒤에만 202 를 준다. AI 호출은 커밋 뒤
턴 실행기(turn_runner.start)가 백그라운드에서 한다. 커밋 뒤 프로세스가 죽어도 남은 QUEUED 실행은
그 원래 입력(`ChatRun.input`)으로 다시 처음 호출할 수 있다.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from pitch_coach_backend.core.database import is_transient_db_error
from pitch_coach_backend.module.chat.api_dto import SendMessageRequest
from pitch_coach_backend.module.chat.api_repository import ChatApiRepository
from pitch_coach_backend.module.chat.entity import ChatConversation, ChatMessage, ChatRun
from pitch_coach_backend.module.chat.exception import (
    ChatBusy,
    ChatDailyLimit,
    ChatNotAccepted,
    ChatRequestConflict,
    ChatRunInProgress,
    ChatTargetNotFound,
)
from pitch_coach_backend.module.chat.repository import ChatRepository
from pitch_coach_backend.module.chat.service.goal_check import SERVICE_DAILY_TURNS, TURN_DEADLINE
from pitch_coach_backend.module.chat.service.run import SEOUL, digest
from pitch_coach_backend.module.pitch.repository import PitchRepository
from pitch_coach_backend.module.take.repository import TakeRepository

USER_DAILY_TURNS = 100
# 대화마다 끝나지 않은 실행을 하나만 두는 부분 유일 인덱스(entity.py ChatRun)
ACTIVE_RUN_INDEX = "uq_chat_runs_one_active"
# 대화 제목은 첫 질문 앞부분이다. 목록 한 줄에 들어갈 만큼만 둔다
TITLE_CHARS = 40


@dataclass(frozen=True)
class Accepted:
    conversation_id: uuid.UUID
    message_id: uuid.UUID
    run_id: uuid.UUID
    # 턴 실행기를 띄울지. 새로 접수했거나 다시 보낸 요청의 실행이 아직 QUEUED 일 때다.
    # 커밋은 됐는데 응답을 잃으면(503) 실행을 띄우지 못한 채 남는다. 다시 보낸 요청이 띄운다.
    # 실행을 잡는 쪽(claim)은 QUEUED 만 잡으므로 두 번 띄워도 한 번만 돈다
    start: bool


def accept_message(
    db: Session, user_id: uuid.UUID, request: SendMessageRequest, now: datetime
) -> Accepted:
    try:
        return _accept_or_replay(db, user_id, request, now)
    except Exception as exc:
        db.rollback()
        if is_transient_db_error(exc):
            raise ChatNotAccepted() from None
        raise


def _accept_or_replay(
    db: Session, user_id: uuid.UUID, request: SendMessageRequest, now: datetime
) -> Accepted:
    request_hash = digest(request.model_dump(mode="json"))
    # 같은 키의 재전송은 실행 중 검사보다 먼저 본다.
    # 그래야 응답을 잃고 다시 보낸 요청이 409 로 막히지 않는다
    replay = _replay(db, user_id, request.client_request_id, request_hash)
    if replay is not None:
        return replay
    try:
        accepted = _accept(db, user_id, request, request_hash, now)
        db.commit()
    except IntegrityError as exc:
        # 같은 키를 동시에 두 번 보냈거나(사용자 · client_request_id 유일),
        # 다른 탭이 같은 대화에 먼저 실행을 만들었다(대화마다 끝나지 않은 실행 하나)
        db.rollback()
        replay = _replay(db, user_id, request.client_request_id, request_hash)
        if replay is not None:
            return replay
        constraint = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
        if constraint == ACTIVE_RUN_INDEX:
            raise ChatRunInProgress() from None
        # 그 밖의 무결성 오류(예: 확인한 뒤 피치가 지워짐)를 진행 중으로 바꿔 말하지 않는다
        raise
    except ChatRunInProgress, ChatDailyLimit, ChatBusy:
        # 같은 키를 동시에 보내면 뒤 요청은 맨 앞 조회에서 앞 요청을 못 보고, 대화 잠금이나
        # 하루 한도 행에서 앞 요청의 커밋을 기다린 뒤 진행 중 · 한도에 막힌다.
        # 그 메시지는 앞 요청이 이미 접수했으니 처음 결과를 준다(L2-05)
        db.rollback()
        replay = _replay(db, user_id, request.client_request_id, request_hash)
        if replay is not None:
            return replay
        raise
    return accepted


def _replay(
    db: Session, user_id: uuid.UUID, client_request_id: uuid.UUID, request_hash: str
) -> Accepted | None:
    message = ChatApiRepository(db).find_request(user_id, client_request_id)
    if message is None:
        return None
    if message.request_hash != request_hash:
        raise ChatRequestConflict()
    run = db.get(ChatRun, message.run_id)
    return Accepted(
        conversation_id=message.conversation_id,
        message_id=message.id,
        run_id=message.run_id,
        start=run is not None and run.status == "QUEUED",
    )


def _accept(
    db: Session,
    user_id: uuid.UUID,
    request: SendMessageRequest,
    request_hash: str,
    now: datetime,
) -> Accepted:
    repo = ChatApiRepository(db)
    conversation = _conversation(db, repo, user_id, request)
    _check_page(db, user_id, request)
    if request.again_of_message_id is not None and (
        repo.get_message_in(request.again_of_message_id, conversation.id) is None
    ):
        raise ChatTargetNotFound()
    if request.choice is not None and (
        ChatRepository(db).find_proposal_card(conversation.id, str(request.choice.proposal_id))
        is None
    ):
        raise ChatTargetNotFound()
    if repo.has_active_run(conversation.id):
        # 승인 대기(WAITING_APPROVAL) 중의 새 메시지는 앞 확인 요청을 취소하고 받아야 한다(12-3).
        # 승인 · 거절 API 와 함께 더한다. 그 전에는 진행 중과 같이 409 로 막는다
        raise ChatRunInProgress()
    _charge(db, repo, user_id, now)

    message = ChatMessage(
        conversation_id=conversation.id,
        user_id=user_id,
        seq=ChatRepository(db).next_message_seq(conversation.id),
        role="user",
        text=request.text,
        client_request_id=request.client_request_id,
        request_hash=request_hash,
        again_of_message_id=request.again_of_message_id,
    )
    _add(db, message)
    run = ChatRun(
        conversation_id=conversation.id,
        user_id=user_id,
        message_id=message.id,
        first_trigger="USER_MESSAGE",
        status="QUEUED",
        scope_pitch_id=conversation.scope_pitch_id,
        # 처음 /agent/turn 요청을 다시 만들 원래 입력. 재시작 복구도 이 값으로 처음 호출한다
        input={
            "user_message": request.text,
            "page": request.page.model_dump(mode="json"),
            "choice": request.choice.model_dump(mode="json") if request.choice else None,
        },
        accepted_at=now,
        deadline_at=now + TURN_DEADLINE,
    )
    _add(db, run)
    message.run_id = run.id
    conversation.last_user_activity_at = now
    if not conversation.title:
        conversation.title = _title(request.text)
    return Accepted(
        conversation_id=conversation.id, message_id=message.id, run_id=run.id, start=True
    )


def _add(db: Session, entity: object) -> None:
    db.add(entity)
    db.flush()


def _conversation(
    db: Session, repo: ChatApiRepository, user_id: uuid.UUID, request: SendMessageRequest
) -> ChatConversation:
    if request.conversation_id is not None:
        # 대화 행을 잠가 같은 대화에 동시에 들어온 메시지를 줄 세운다
        conversation = repo.get_owned_conversation(request.conversation_id, user_id, lock=True)
        if conversation is None:
            raise ChatTargetNotFound()
        return conversation
    if request.scope_pitch_id is not None and (
        PitchRepository(db).get_owned(request.scope_pitch_id, user_id) is None
    ):
        raise ChatTargetNotFound()
    conversation = ChatConversation(
        user_id=user_id, scope_pitch_id=request.scope_pitch_id, title=""
    )
    _add(db, conversation)
    return conversation


def _check_page(db: Session, user_id: uuid.UUID, request: SendMessageRequest) -> None:
    """화면 상태의 피치 · Take 도 이 사용자의 것인지 본다. AI 요청에 그대로 실리기 때문이다."""
    page = request.page
    if page.pitch_id is not None and PitchRepository(db).get_owned(page.pitch_id, user_id) is None:
        raise ChatTargetNotFound()
    if page.take_id is not None and TakeRepository(db).get_owned(page.take_id, user_id) is None:
        raise ChatTargetNotFound()


def _charge(db: Session, repo: ChatApiRepository, user_id: uuid.UUID, now: datetime) -> None:
    day = now.astimezone(SEOUL).date()
    retry_after = {"Retry-After": str(seconds_until_tomorrow(now))}
    if not repo.charge_turn(user_id, day, USER_DAILY_TURNS):
        raise ChatDailyLimit(headers=retry_after)
    # 서비스 전체 하루 상한에 처음 호출 1회를 예약한다. 보내지 않고 끝나면 돌려준다(실행 취소)
    if not ChatRepository(db).reserve_service_turn(day, SERVICE_DAILY_TURNS):
        raise ChatBusy(headers=retry_after)


def seconds_until_tomorrow(now: datetime) -> int:
    """다음 Asia/Seoul 00:00 까지 남은 초. 하루 한도의 Retry-After 로 쓴다."""
    local = now.astimezone(SEOUL)
    tomorrow = datetime.combine(local.date() + timedelta(days=1), datetime.min.time(), SEOUL)
    return max(1, int((tomorrow - local).total_seconds()))


def _title(text: str) -> str:
    first = " ".join(text.split())
    return first if len(first) <= TITLE_CHARS else first[: TITLE_CHARS - 1] + "…"
