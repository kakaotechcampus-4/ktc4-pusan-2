"""실행 상태 조회 · 실행 취소(chat-agent.md 12-3). 화면은 실행 중 1초마다 조회한다.

AI 의 실행 상태(`state`, 서명 포함)는 화면에 보내지 않는다. 화면은 run_id · action_id 만 다룬다.
"""

import json
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from pitch_coach_backend.module.chat.api_dto import (
    RESPONSE_MAX_BYTES,
    RunErrorDTO,
    RunEventDTO,
    RunStatusResponse,
)
from pitch_coach_backend.module.chat.api_repository import ChatApiRepository
from pitch_coach_backend.module.chat.entity import ChatRun
from pitch_coach_backend.module.chat.exception import ChatTargetNotFound
from pitch_coach_backend.module.chat.repository import ChatRepository
from pitch_coach_backend.module.chat.service.conversation import owned_sources
from pitch_coach_backend.module.chat.service.goal_check import close_goal_lease
from pitch_coach_backend.module.chat.service.run import mark_cancelled
from pitch_coach_backend.module.chat.service.stale import run_source_ids, uses_deleted
from pitch_coach_backend.module.chat.service.waiting import (
    CANCELLED_TEXT,
    close_waiting,
    read_emitted,
)

# 화면이 바로 취소할 수 있는 상태.
# 승인 대기는 대기 행동을 함께 끝내고 고정 문장을 남겨서 따로 처리한다
CANCELLABLE = ("QUEUED", "RUNNING")
EVENTS_PAGE = 200


def run_status(
    db: Session, user_id: uuid.UUID, run_id: uuid.UUID, after_seq: int = 0
) -> RunStatusResponse:
    run = ChatApiRepository(db).get_owned_run(run_id, user_id)
    if run is None:
        raise ChatTargetNotFound()
    return _view(db, run, after_seq)


def latest_run_status(
    db: Session, user_id: uuid.UUID, conversation_id: uuid.UUID, after_seq: int = 0
) -> RunStatusResponse | None:
    """화면을 떠났다 오거나 새로고침했을 때 대화 ID 로 마지막 실행을 다시 찾는다."""
    repo = ChatApiRepository(db)
    if repo.get_owned_conversation(conversation_id, user_id) is None:
        raise ChatTargetNotFound()
    run = repo.latest_run(conversation_id)
    return _view(db, run, after_seq) if run is not None else None


def cancel_run(db: Session, user_id: uuid.UUID, run_id: uuid.UUID) -> RunStatusResponse:
    """실행을 CANCELLED 로 끝낸다. 이미 끝났으면 그 상태를 그대로 돌려준다(먼저 커밋한 쪽이 이긴다).

    턴 실행기는 저장할 때마다 row_revision 을 조건으로 행을 잠그므로, 여기서 올린 판 번호 뒤에
    도착한 AI 결과 · 행동은 반영되지 않는다.
    """
    repo = ChatApiRepository(db)
    peek = repo.get_owned_run(run_id, user_id)
    if peek is not None and peek.status == "WAITING_APPROVAL":
        return _cancel_waiting(db, peek)
    run = repo.get_owned_run(run_id, user_id, lock=True)
    if run is None:
        db.rollback()
        raise ChatTargetNotFound()
    if run.status not in CANCELLABLE:
        # 잠근 채 읽은 상태로 답한다. rollback 하면 객체가 만료돼 다시 읽게 된다
        view = _view(db, run)
        db.rollback()
        return view
    chat_repo = ChatRepository(db)
    if run.first_trigger == "TAKE_REVIEWED":
        # 임대가 PENDING 으로 남으면 5분 임대가 풀린 뒤 주기 작업이 같은 리뷰로 실행을 다시
        # 만들고 확인 횟수를 또 줄인다
        close_goal_lease(chat_repo, run)
    mark_cancelled(chat_repo, run, datetime.now(UTC))
    db.commit()
    return _view(db, run)


def _cancel_waiting(db: Session, run: ChatRun) -> RunStatusResponse:
    """승인 대기 중인 실행의 취소. 행동까지 끝내고 고정 문장을 남긴다. AI 는 부르지 않는다."""
    repo = ChatApiRepository(db)
    try:
        emitted = read_emitted(db, run.id)
        repo.lock_conversation_of(run)
        db.refresh(run, with_for_update=True)
        if run.status == "WAITING_APPROVAL":
            close_waiting(
                db,
                run,
                repo.waiting_action(run.id),
                emitted,
                action_status="CANCELLED",
                run_status="CANCELLED",
                text=CANCELLED_TEXT,
                now=datetime.now(UTC),
            )
        db.commit()
    except Exception:
        db.rollback()
        raise
    return view_of(db, run)


def view_of(db: Session, run: ChatRun) -> RunStatusResponse:
    """이벤트 없이 상태만. 취소 · 승인 · 거절의 응답이다. 이벤트는 화면이 자기 커서로 조회한다."""
    return _view(db, run)


def _view(db: Session, run: ChatRun, after_seq: int | None = None) -> RunStatusResponse:
    """실행 상태. `after_seq` 를 주면 그 뒤의 실행 이벤트까지 싣는다."""
    hidden = _uses_deleted_sources(db, run)
    plan = None if hidden else run.plan
    trace = None if hidden else run.trace
    cards = [] if hidden else _waiting_cards(db, run)
    events, has_more, next_after_seq = (
        ([], False, None)
        if after_seq is None
        else _events_after(db, run, after_seq, hidden, _json_size([plan, trace, cards]))
    )
    return RunStatusResponse(
        run_id=run.id,
        conversation_id=run.conversation_id,
        status=run.status,
        plan=plan,
        trace=trace,
        events=events,
        next_after_seq=next_after_seq,
        has_more=has_more,
        cards=cards,
        error=RunErrorDTO(code=run.error_code) if run.error_code else None,
        retry_after_s=run.retry_after_s,
    )


def _json_size(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def _events_after(
    db: Session, run: ChatRun, after_seq: int, hidden: bool, used_bytes: int
) -> tuple[list[RunEventDTO], bool, int]:
    """커서 뒤의 이벤트를 순번순으로. 크기 한도를 넘으면 나누고 문장 · 근거를 자르지 않는다.

    첫 이벤트는 크기와 상관없이 싣는다. 안 그러면 커서가 나아가지 못한다.
    """
    rows = ChatApiRepository(db).list_run_events(run.id, after_seq=after_seq, limit=EVENTS_PAGE + 1)
    masked = _masked_seqs(db, run, rows) if not hidden else {row.event_seq for row in rows}
    events: list[RunEventDTO] = []
    total = used_bytes
    has_more = False
    for index, row in enumerate(rows):
        data = None if row.event_seq in masked else row.data
        total += _json_size(data)
        if index == EVENTS_PAGE or (events and total > RESPONSE_MAX_BYTES):
            has_more = True
            break
        events.append(
            RunEventDTO(
                event_seq=row.event_seq,
                event=row.event,
                data=data,
                masked=row.event_seq in masked,
            )
        )
    return events, has_more, events[-1].event_seq if events else after_seq


def _masked_seqs(db: Session, run: ChatRun, rows: list[Any]) -> set[int]:
    """문장 줄의 근거가 지워졌거나 남의 것인 이벤트의 순번."""
    by_seq = {row.event_seq: _claim_sources(row.data) for row in rows if row.event == "claim"}
    pitch_ids = {value for pitches, _ in by_seq.values() for value in pitches}
    take_ids = {value for _, takes in by_seq.values() for value in takes}
    if not pitch_ids and not take_ids:
        return set()
    current = owned_sources(db, run.user_id, pitch_ids, take_ids)
    return {
        seq for seq, (pitches, takes) in by_seq.items() if uses_deleted(pitches, takes, current)
    }


def _claim_sources(data: Any) -> tuple[set[str], set[str]]:
    evidence = data.get("evidence") if isinstance(data, dict) else None
    items = [item for item in evidence or [] if isinstance(item, dict)]
    return (
        {str(item["pitch_id"]) for item in items if item.get("pitch_id")},
        {str(item["take_id"]) for item in items if item.get("take_id")},
    )


def _waiting_cards(db: Session, run: ChatRun) -> list[dict[str, Any]]:
    """승인 대기 중인 행동의 확인 카드. 카드는 턴 실행기가 저장한 답 안에 있다.

    AI 가 그린 값을 그대로 주되 해시는 저장한 대기 행동의 값으로 맞춘다(12-3 '승인 대기').
    """
    if run.status != "WAITING_APPROVAL":
        return []
    action = ChatApiRepository(db).waiting_action(run.id)
    message = (run.interim_message or {}).get("message") or {}
    if action is None:
        return []
    return [
        {**card, "args_hash": action.args_hash}
        for card in message.get("cards") or []
        if card.get("type") == "ASK_CONFIRM" and card.get("action_id") == action.action_id
    ]


def _uses_deleted_sources(db: Session, run: ChatRun) -> bool:
    """실행이 읽은 피치 · Take 가 지워졌거나 남의 것인지. 진행 전체를 가릴 때 쓴다.

    정리 작업(cleanup.py `_mask_runs`)이 1시간마다 비우지만 그 사이에도 보이면 안 되므로
    메시지 목록처럼 읽을 때 다시 본다(12-2 ①). 보는 근거는 `run_source_ids` 로 정리 작업과 같다.
    """
    pitch_ids, take_ids = run_source_ids(
        run.read_sources, ChatApiRepository(db).action_source_take_ids(run.id)
    )
    if not pitch_ids and not take_ids:
        return False
    current = owned_sources(db, run.user_id, pitch_ids, take_ids)
    return uses_deleted(
        {str(value) for value in pitch_ids}, {str(value) for value in take_ids}, current
    )
