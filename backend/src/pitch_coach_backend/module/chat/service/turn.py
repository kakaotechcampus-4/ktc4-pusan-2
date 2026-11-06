import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from sqlalchemy.orm import Session

from pitch_coach_backend.module.chat.ai_client import TurnFailure, TurnFinal
from pitch_coach_backend.module.chat.ai_response import UPSTREAM_ERROR, StreamLine
from pitch_coach_backend.module.chat.entity import (
    ChatAction,
    ChatConversation,
    ChatDiagnostic,
    ChatMessage,
    ChatRun,
    ChatRunEvent,
)
from pitch_coach_backend.module.chat.repository import ChatRepository
from pitch_coach_backend.module.chat.service.goal_check import settle_goal_check
from pitch_coach_backend.module.chat.service.memory import (
    memory_cards,
    save_profile_updates,
    save_summary,
)
from pitch_coach_backend.module.chat.service.practice import start_practice
from pitch_coach_backend.module.chat.service.request import goal_view
from pitch_coach_backend.module.chat.service.result import failed, save_result, succeeded
from pitch_coach_backend.module.chat.service.run import (
    LEASE,
    OWNER,
    SEOUL,
    ClaimedRun,
    add_run_log,
    cancel,
    check_sources,
    claimed,
    digest,
    lock_conversation_of,
    lock_current,
    lock_ids,
    lock_or_cancel,
    release_unsent,
    take_lease,
)

logger = logging.getLogger(__name__)


class ScopeChangeRejected(Exception):
    pass


class SourceDeleted(Exception):
    pass


def mark_sending(
    db: Session,
    run: ClaimedRun,
    turn_call: int,
    request_id: str,
    sources: dict[str, list[str]],
    memory_version: int,
    summary_version: int | None,
) -> ClaimedRun | None:
    row = lock_current(db, run)
    if row is None:
        return None
    repo = ChatRepository(db)
    if turn_call == 1:
        repo.release_service_turn(row.accepted_at.astimezone(SEOUL).date())
    repo.add_service_turn(datetime.now(SEOUL).date())
    row.turn_calls = [
        *(row.turn_calls or []),
        {
            "turn_call": turn_call,
            "request_id": request_id,
            "state_revision": row.state_revision if turn_call > 1 else None,
            "stage": "SENDING",
            "sources": sources,
            "memory_version": memory_version,
            "summary_version": summary_version,
        },
    ]
    row.row_revision += 1
    db.commit()
    return claimed(row)


def save_progress(db: Session, run: ClaimedRun, turn_call: int, line: StreamLine) -> bool:
    row = lock_current(db, run)
    if row is None:
        return False
    repo = ChatRepository(db)
    repo.add(
        ChatRunEvent(
            run_id=row.id,
            event_seq=repo.next_event_seq(row.id),
            event=line.event,
            data=line.data,
        )
    )
    row.progress_turn_call = turn_call
    row.progress_seq = line.seq
    if line.event == "plan":
        row.plan = line.data.get("plan")
    elif line.event == "tool_done":
        row.trace = [*(row.trace or []), line.data.get("trace")]
    db.commit()
    return True


def wait_for_action(
    db: Session, run: ClaimedRun, turn_call: int, final: TurnFinal
) -> ClaimedRun | None:
    data = final.data
    pitch_ids, take_ids = _answer_sources(data)
    check_sources(db, run, pitch_ids, take_ids)
    lock_or_cancel(db, run, pitch_ids, take_ids)
    repo = ChatRepository(db)
    conversation = lock_conversation_of(db, run)
    row = lock_current(db, run)
    if row is None:
        return None
    if _sent_claims_differ(repo, row, data):
        _end_with_sent_claims(db, run, row, turn_call, final)
        return None
    action = data["actions"][0]
    row.state = data["state"]
    row.budget_used = data["budget_used"]
    row.usage = data.get("usage")
    row.plan = data.get("plan")
    row.trace = data.get("trace")
    if data.get("message") is not None:
        row.interim_message = {
            "message": data["message"],
            "assessments": data.get("assessments"),
            "evidence": data.get("evidence"),
            "memory_saved": memory_cards(row),
        }
    row.turn_calls = [
        {**call, "stage": "DONE"} if call["turn_call"] == turn_call else call
        for call in row.turn_calls or []
    ]
    repo.add(
        ChatAction(
            conversation_id=row.conversation_id,
            user_id=row.user_id,
            run_id=row.id,
            action_id=action["action_id"],
            type=action["type"],
            args=action["args"],
            args_digest=digest(action["args"]),
            autonomy=action["autonomy"],
            idempotency_key=action["idempotency_key"],
            args_hash=action["args_hash"],
            status="PENDING",
        )
    )
    _add_diagnostics(repo, row, turn_call, final.diagnostics, datetime.now(UTC))
    if _apply_scope(row, conversation, data):
        row.state_revision += 1
    row.row_revision += 1
    db.commit()
    return claimed(row)


@dataclass(frozen=True)
class ResumePoint:
    run: ClaimedRun
    action: dict[str, Any]
    turn_call: int
    state: dict[str, Any]
    budget_used: dict[str, Any]


def resume_point(db: Session, run_id: uuid.UUID) -> ResumePoint | None:
    repo = ChatRepository(db)
    row = repo.lock_run(run_id, statuses=("RUNNING",))
    if row is None or not row.turn_calls:
        return None
    saved = repo.get_approved_action(row.id)
    if saved is None:
        return None
    return _resume_from(db, row, saved)


def recovery_point(db: Session, run_id: uuid.UUID) -> ResumePoint | None:
    repo = ChatRepository(db)
    row = repo.lock_run(run_id, statuses=("RUNNING",))
    if row is None or not row.turn_calls or row.state is None:
        return None
    saved = repo.get_last_action(row.id)
    if saved is None:
        return None
    return _resume_from(db, row, saved)


def _resume_from(db: Session, row: ChatRun, saved: ChatAction) -> ResumePoint:
    take_lease(row)
    point = ResumePoint(
        run=claimed(row),
        action=_action_view(saved),
        turn_call=row.turn_calls[-1]["turn_call"],
        state=row.state,
        budget_used=row.budget_used,
    )
    db.commit()
    return point


def _action_view(saved: ChatAction) -> dict[str, Any]:
    return {
        "action_id": saved.action_id,
        "type": saved.type,
        "args": saved.args,
        "autonomy": saved.autonomy,
        "args_hash": saved.args_hash,
        "authorized_by": None,
    }


def finish(db: Session, run: ClaimedRun, turn_call: int, final: TurnFinal) -> bool:
    data = final.data
    pitch_ids, take_ids = _answer_sources(data)
    if run.first_trigger == "TAKE_REVIEWED":
        take_ids.add(data["goal_result"]["take_id"])
    check_sources(db, run, pitch_ids, take_ids)
    lock_or_cancel(db, run, pitch_ids, take_ids)
    repo = ChatRepository(db)
    conversation = lock_conversation_of(db, run)
    row = lock_current(db, run)
    if row is None:
        return False
    if _sent_claims_differ(repo, row, data):
        _end_with_sent_claims(db, run, row, turn_call, final)
        return True
    if row.first_trigger == "TAKE_REVIEWED" and not settle_goal_check(
        repo, row, conversation, data["goal_result"]
    ):
        cancel(db, run)
        return False
    _apply_scope(row, conversation, data)
    start_practice(db, repo, row, data)
    save_profile_updates(repo, row, conversation, data)
    save_summary(repo, row, turn_call, data)
    message = data["message"]
    now = datetime.now(UTC)
    repo.add(
        ChatMessage(
            conversation_id=conversation.id,
            user_id=row.user_id,
            seq=repo.next_message_seq(conversation.id),
            role="assistant",
            text=message["text"],
            status=data["status"],
            run_id=row.id,
            claims=message["claims"],
            cards=message["cards"],
            sources=message["sources"],
            assessments=data.get("assessments"),
            plan=data.get("plan"),
            trace=data.get("trace"),
            basis=data.get("basis"),
            evidence=data.get("evidence"),
            usage=data.get("usage"),
            meta=data.get("meta"),
            source_pitch_ids=sorted(pitch_ids),
            source_take_ids=sorted(take_ids),
        )
    )
    row.status = data["status"]
    row.plan = data.get("plan")
    row.trace = data.get("trace")
    row.budget_used = data["budget_used"]
    row.usage = data.get("usage")
    row.finished_at = now
    row.row_revision += 1
    row.turn_calls = [
        {**call, "stage": "DONE"} if call["turn_call"] == turn_call else call
        for call in row.turn_calls or []
    ]
    add_run_log(repo, row, now)
    _add_diagnostics(repo, row, turn_call, final.diagnostics, now)
    db.commit()
    return True


def fail(db: Session, run: ClaimedRun, failure: TurnFailure) -> bool:
    repo = ChatRepository(db)
    lock_conversation_of(db, run)
    row = lock_current(db, run)
    if row is None:
        return False
    try:
        message = save_claim_message(db, row, "PARTIAL")
    except SourceDeleted:
        db.rollback()
        cancel(db, run)
        return True
    now = datetime.now(UTC)
    release_unsent(repo, row)
    row.status = "PARTIAL" if message else "FAILED"
    row.error_code = failure.code
    row.retry_after_s = failure.retry_after_s
    row.finished_at = now
    row.row_revision += 1
    add_run_log(repo, row, now)
    if row.turn_calls:
        _add_diagnostics(repo, row, row.turn_calls[-1]["turn_call"], failure.diagnostics, now)
    db.commit()
    return True


def recover_run(db: Session, run_id: uuid.UUID, now: datetime) -> Literal["START", "RESUME"] | None:
    repo = ChatRepository(db)
    row = repo.lock_run(run_id, statuses=("QUEUED", "RUNNING"))
    if row is None or (row.lease_until is not None and row.lease_until > now):
        db.rollback()
        return None
    row.lease_owner = OWNER
    row.lease_until = now + LEASE
    for saved in repo.list_actions(row.id):
        if saved.status == "RUNNING":
            _settle_action(repo, saved)
    row.row_revision += 1
    last = row.turn_calls[-1] if row.turn_calls else None
    if row.deadline_at > now:
        if last is None:
            row.status = "QUEUED"
            db.commit()
            return "START"
        if last["stage"] == "DONE":
            step = _after_needs_action(repo, row, now)
            if step != "END":
                db.commit()
                return "RESUME" if step == "RESUME" else None
    if last is not None and last["stage"] == "SENDING":
        row.usage = {**(row.usage or {}), "measured": False, "missing_reason": "RESPONSE_LOST"}
    row.status = "RUNNING"
    db.commit()
    code = "AGENT_TIMEOUT" if row.deadline_at <= now else UPSTREAM_ERROR
    fail(db, claimed(row), TurnFailure(code))
    return None


def _after_needs_action(
    repo: ChatRepository, row: ChatRun, now: datetime
) -> Literal["RESUME", "WAIT", "END"]:
    saved = repo.get_last_action(row.id)
    if saved is None or row.state is None or saved.status not in ("PENDING", "SUCCEEDED", "FAILED"):
        return "END"
    if saved.autonomy == "ASK" and saved.status == "PENDING" and not saved.approved:
        if _has_confirm_card(row, saved.action_id):
            row.status = "WAITING_APPROVAL"
            row.approval_started_at = now
            return "WAIT"
        _settle_action(repo, saved)
    return "RESUME"


def _settle_action(repo: ChatRepository, saved: ChatAction) -> None:
    action = _action_view(saved)
    goal = repo.get_goal_from_action(saved.id) if saved.type == "SAVE_GOAL" else None
    if goal is not None:
        result = succeeded(action, {"goal": goal_view(goal)})
    else:
        result = failed(action, "IDEMPOTENCY_CONFLICT", "작업 완료를 확인하지 못했어요.")
    save_result(saved, result)


def _has_confirm_card(row: ChatRun, action_id: str) -> bool:
    cards = ((row.interim_message or {}).get("message") or {}).get("cards") or []
    return any(
        card.get("type") == "ASK_CONFIRM" and card.get("action_id") == action_id for card in cards
    )


def _sent_claims_differ(repo: ChatRepository, row: ChatRun, data: dict[str, Any]) -> bool:
    sent = [event.data["claim"] for event in repo.list_claim_events(row.id)]
    claims = (data.get("message") or {}).get("claims") or []
    return claims[: len(sent)] != sent


def _end_with_sent_claims(
    db: Session, run: ClaimedRun, row: ChatRun, turn_call: int, final: TurnFinal
) -> None:
    logger.warning("final 앞부분이 보낸 claim 과 다르다 run=%s call=%s", row.id, turn_call)
    try:
        save_claim_message(db, row, "PARTIAL")
    except SourceDeleted:
        db.rollback()
        cancel(db, run)
        return
    repo = ChatRepository(db)
    now = datetime.now(UTC)
    row.status = "PARTIAL"
    row.budget_used = final.data["budget_used"]
    row.usage = final.data.get("usage")
    row.finished_at = now
    row.row_revision += 1
    row.turn_calls = [
        {**call, "stage": "DONE"} if call["turn_call"] == turn_call else call
        for call in row.turn_calls or []
    ]
    add_run_log(repo, row, now)
    _add_diagnostics(repo, row, turn_call, final.diagnostics, now)
    db.commit()


def save_claim_message(db: Session, row: ChatRun, status: str) -> ChatMessage | None:
    repo = ChatRepository(db)
    events = repo.list_claim_events(row.id)
    if not events:
        return None
    claims = [event.data["claim"] for event in events]
    evidence = [item for event in events for item in event.data.get("evidence") or []]
    sources = [item for event in events for item in event.data.get("sources") or []]
    assessments = [item for event in events for item in event.data.get("assessments") or []]
    pitch_ids, take_ids = _answer_sources({"evidence": evidence})
    if not lock_ids(db, pitch_ids, take_ids):
        raise SourceDeleted(row.id)
    conversation = repo.lock_conversation(row.conversation_id)
    message = ChatMessage(
        conversation_id=conversation.id,
        user_id=row.user_id,
        seq=repo.next_message_seq(conversation.id),
        role="assistant",
        text="\n".join(claim["text"] for claim in claims),
        status=status,
        run_id=row.id,
        claims=claims,
        cards=memory_cards(row),
        sources=sources,
        assessments=assessments,
        plan=row.plan,
        trace=row.trace,
        evidence=evidence,
        usage=row.usage,
        source_pitch_ids=sorted(pitch_ids),
        source_take_ids=sorted(take_ids),
    )
    repo.add(message)
    return message


def _apply_scope(row: ChatRun, conversation: ChatConversation, data: dict[str, Any]) -> bool:
    if row.first_trigger == "TAKE_REVIEWED":
        return False
    new = data["scope_pitch_id"]
    current = str(conversation.scope_pitch_id) if conversation.scope_pitch_id else None
    if new == current:
        return False
    if new is None or new not in (row.read_sources or {}).get("pitches", []):
        raise ScopeChangeRejected(new)
    conversation.scope_pitch_id = uuid.UUID(new)
    return True


def _answer_sources(data: dict[str, Any]) -> tuple[set[str], set[str]]:
    pitch_ids: set[str] = set()
    take_ids: set[str] = set()
    for item in data.get("evidence") or []:
        pitch_ids.add(item.get("pitch_id"))
        take_ids.add(item.get("take_id"))
    basis = data.get("basis")
    for pitch in basis.get("pitches") or [] if isinstance(basis, dict) else []:
        pitch_ids.add(pitch.get("pitch_id"))
        take_ids.update(take.get("take_id") for take in pitch.get("takes") or [])
    pitch_ids.discard(None)
    take_ids.discard(None)
    return pitch_ids, take_ids


def _add_diagnostics(
    repo: ChatRepository,
    row: ChatRun,
    turn_call: int,
    diagnostics: dict[str, Any] | None,
    now: datetime,
) -> None:
    if diagnostics is None:
        return
    call = next(call for call in row.turn_calls if call["turn_call"] == turn_call)
    repo.add(
        ChatDiagnostic(
            conversation_id=row.conversation_id,
            user_id=row.user_id,
            run_id=row.id,
            turn_call=turn_call,
            request_id=call["request_id"],
            diagnostics=diagnostics,
            received_at=now,
        )
    )
