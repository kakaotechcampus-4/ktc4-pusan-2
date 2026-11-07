"""승인 대기(WAITING_APPROVAL)를 AI 를 부르지 않고 끝내는 일(chat-agent.md 12-3 · 7-10).

거절 · 만료 · 취소 · 대기 중 새 메시지가 모두 같은 일을 한다. 이미 낸 문장 뒤에 고정 문장을 덧붙여
메시지로 저장하고 행동과 실행을 끝낸다. 낸 문장은 지우지 않고 판단 · 근거도 함께 남긴다.

잠그는 순서는 피치 · Take(공유) → 대화 → 실행이다. 피치 삭제 · 턴 실행기와 같은 순서라
서로 막지 않는다.
그래서 낸 문장의 근거를 대화를 잠그기 전에 `read_emitted` 로 먼저 잠근다.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from pitch_coach_backend.module.chat.api_repository import ChatApiRepository
from pitch_coach_backend.module.chat.entity import ChatAction, ChatMessage, ChatRun
from pitch_coach_backend.module.chat.repository import ChatRepository
from pitch_coach_backend.module.chat.service.memory import memory_cards
from pitch_coach_backend.module.chat.service.run import (
    add_run_log,
    canonical_ids,
    end_approval_wait,
    lock_ids,
)

APPROVAL_WINDOW = timedelta(minutes=30)
EXPIRED_TEXT = "확인 시간이 지나 실행하지 않았어요. 필요하면 다시 말해 주세요."
NEW_MESSAGE_TEXT = "새 메시지를 받아 앞의 확인 요청은 취소했어요."
CANCELLED_TEXT = "확인 요청을 취소했어요."
NOT_DONE_TEXT = {
    "SAVE_GOAL": "목표를 저장하지 않았어요.",
    "REASSESS": "다시 평가하지 않았어요.",
}


@dataclass(frozen=True)
class Emitted:
    """이 실행에서 이미 낸 문장과 그 판단 · 근거. 근거가 지워졌으면 모두 비운다."""

    claims: list[dict[str, Any]]
    evidence: list[Any]
    sources: list[Any]
    assessments: list[Any]
    pitch_ids: list[str]
    take_ids: list[str]


def read_emitted(db: Session, run_id: uuid.UUID) -> Emitted:
    """실행 이벤트에서 낸 문장을 모으고 근거 피치 · Take 를 잠근다(삭제와 겹칠 때, 12-2)."""
    claims: list[dict[str, Any]] = []
    evidence: list[Any] = []
    sources: list[Any] = []
    assessments: list[Any] = []
    for event in ChatRepository(db).list_claim_events(run_id):
        data = event.data
        claims.append(data["claim"])
        evidence.extend(item for item in data.get("evidence") or [] if isinstance(item, dict))
        sources.extend(data.get("sources") or [])
        assessments.extend(data.get("assessments") or [])
    pitch_ids = {str(item["pitch_id"]) for item in evidence if item.get("pitch_id")}
    take_ids = {str(item["take_id"]) for item in evidence if item.get("take_id")}
    if not lock_ids(db, pitch_ids, take_ids):
        # 지운 기록이 대화에 되살아나지 않게 고정 문장만 남긴다
        return Emitted([], [], [], [], [], [])
    return Emitted(
        claims, evidence, sources, assessments, canonical_ids(pitch_ids), canonical_ids(take_ids)
    )


def close_waiting(
    db: Session,
    run: ChatRun,
    action: ChatAction | None,
    emitted: Emitted,
    *,
    action_status: str,
    run_status: str,
    text: str,
    now: datetime,
) -> None:
    """행동 · 실행을 끝내고 고정 문장을 덧붙인 메시지를 넣는다. 커밋은 부르는 쪽이 한다."""
    if action is not None:
        action.status = action_status
        if action_status == "REJECTED":
            action.approved = False
            action.approved_at = now
    run.status = run_status
    run.state = None
    end_approval_wait(run, now)
    run.finished_at = now
    run.row_revision += 1
    if run_status in ("EXPIRED", "CANCELLED"):
        run.error_code = f"AGENT_{run_status}"
    chat_repo = ChatRepository(db)
    add_run_log(chat_repo, run, now)
    claims = [*emitted.claims, _fixed_claim(text)]
    chat_repo.add(
        ChatMessage(
            conversation_id=run.conversation_id,
            user_id=run.user_id,
            seq=chat_repo.next_message_seq(run.conversation_id),
            role="assistant",
            text="\n".join(claim["text"] for claim in claims),
            # 메시지 상태에는 만료 · 취소가 없다. 답을 끝까지 내지 못했으니 PARTIAL 이다
            status="DONE" if run_status == "DONE" else "PARTIAL",
            run_id=run.id,
            claims=claims,
            # 승인 대기 때 저장한 기억은 결정과 상관없이 남는다. 카드도 함께 남긴다
            cards=memory_cards(run),
            sources=emitted.sources,
            assessments=emitted.assessments,
            plan=run.plan,
            trace=run.trace,
            evidence=emitted.evidence,
            usage=run.usage,
            source_pitch_ids=emitted.pitch_ids,
            source_take_ids=emitted.take_ids,
        )
    )
    db.flush()


def _fixed_claim(text: str) -> dict[str, Any]:
    return {
        "kind": "SYSTEM",
        "assessment_id": None,
        "text": text,
        "evidence_ids": [],
        "source_ids": [],
        "action_id": None,
    }


def overdue_run_ids(db: Session, now: datetime) -> list[uuid.UUID]:
    return ChatApiRepository(db).overdue_waiting_run_ids(now - APPROVAL_WINDOW)


def expire_run(db: Session, run_id: uuid.UUID, now: datetime) -> bool:
    """30분이 지난 승인 대기를 끝낸다. 그사이 결정됐으면 건드리지 않는다."""
    try:
        emitted = read_emitted(db, run_id)
        repo = ChatApiRepository(db)
        run = db.get(ChatRun, run_id)
        if run is None or repo.lock_conversation_of(run) is None:
            db.rollback()
            return False
        db.refresh(run, with_for_update=True)
        if run.status != "WAITING_APPROVAL" or not is_overdue(run, now):
            db.rollback()
            return False
        close_waiting(
            db,
            run,
            repo.waiting_action(run.id),
            emitted,
            action_status="EXPIRED",
            run_status="EXPIRED",
            text=EXPIRED_TEXT,
            now=now,
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    return True


def is_overdue(run: ChatRun, now: datetime) -> bool:
    started = run.approval_started_at
    return started is not None and now - started >= APPROVAL_WINDOW
