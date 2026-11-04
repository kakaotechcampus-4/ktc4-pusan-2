"""실행 상태 조회 · 실행 취소(chat-agent.md 12-3). 화면은 실행 중 1초마다 조회한다.

AI 의 실행 상태(`state`, 서명 포함)는 화면에 보내지 않는다. 화면은 run_id · action_id 만 다룬다.
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from pitch_coach_backend.module.chat.api_dto import RunErrorDTO, RunStatusResponse
from pitch_coach_backend.module.chat.api_repository import ChatApiRepository
from pitch_coach_backend.module.chat.entity import ChatRun
from pitch_coach_backend.module.chat.exception import ChatTargetNotFound
from pitch_coach_backend.module.chat.repository import ChatRepository
from pitch_coach_backend.module.chat.service.run import SEOUL, add_run_log

# 화면이 바로 취소할 수 있는 상태. 승인 대기(WAITING_APPROVAL)의 취소는 대기 행동을 함께 끝내고
# 고정 문장을 남겨야 해서 승인 · 거절 API 와 함께 더한다
CANCELLABLE = ("QUEUED", "RUNNING")


def run_status(db: Session, user_id: uuid.UUID, run_id: uuid.UUID) -> RunStatusResponse:
    run = ChatApiRepository(db).get_owned_run(run_id, user_id)
    if run is None:
        raise ChatTargetNotFound()
    return _view(run)


def latest_run_status(
    db: Session, user_id: uuid.UUID, conversation_id: uuid.UUID
) -> RunStatusResponse | None:
    """화면을 떠났다 오거나 새로고침했을 때 대화 ID 로 마지막 실행을 다시 찾는다."""
    repo = ChatApiRepository(db)
    if repo.get_owned_conversation(conversation_id, user_id) is None:
        raise ChatTargetNotFound()
    run = repo.latest_run(conversation_id)
    return _view(run) if run is not None else None


def cancel_run(db: Session, user_id: uuid.UUID, run_id: uuid.UUID) -> RunStatusResponse:
    """실행을 CANCELLED 로 끝낸다. 이미 끝났으면 그 상태를 그대로 돌려준다(먼저 커밋한 쪽이 이긴다).

    턴 실행기는 저장할 때마다 row_revision 을 조건으로 행을 잠그므로, 여기서 올린 판 번호 뒤에
    도착한 AI 결과 · 행동은 반영되지 않는다.
    """
    repo = ChatApiRepository(db)
    run = repo.get_owned_run(run_id, user_id, lock=True)
    if run is None:
        db.rollback()
        raise ChatTargetNotFound()
    if run.status not in CANCELLABLE:
        # 잠근 채 읽은 상태로 답한다. rollback 하면 객체가 만료돼 다시 읽게 된다
        view = _view(run)
        db.rollback()
        return view
    now = datetime.now(UTC)
    if run.first_trigger == "TAKE_REVIEWED":
        _close_goal_check(ChatRepository(db), run)
    if not run.turn_calls:
        # 아직 /agent/turn 을 보내지 않았다. 접수 때 잡은 서비스 전체 예약을 돌려준다
        repo.release_service_turn(run.accepted_at.astimezone(SEOUL).date())
    run.status = "CANCELLED"
    run.state = None
    run.error_code = "AGENT_CANCELLED"
    run.finished_at = now
    run.row_revision += 1
    add_run_log(ChatRepository(db), run, now)
    db.commit()
    return _view(run)


def _close_goal_check(repo: ChatRepository, run: ChatRun) -> None:
    """자동 목표 확인 실행을 취소하면 그 확인도 끝난 것으로 닫는다.

    임대가 PENDING 으로 남으면 5분 임대가 풀린 뒤 주기 작업이 같은 리뷰로 실행을 다시 만들고
    확인 횟수를 또 줄인다. 실행 → 임대 순서로 잠근다(턴 실행기의 settle_goal_check 와 같다).
    """
    lease = repo.lock_goal_check(run.id)
    if lease is not None and lease.status == "PENDING" and lease.fencing_token == run.fencing_token:
        lease.status = "DONE"


def _view(run: ChatRun) -> RunStatusResponse:
    return RunStatusResponse(
        run_id=run.id,
        conversation_id=run.conversation_id,
        status=run.status,
        plan=run.plan,
        trace=run.trace,
        error=RunErrorDTO(code=run.error_code) if run.error_code else None,
        retry_after_s=run.retry_after_s,
    )
