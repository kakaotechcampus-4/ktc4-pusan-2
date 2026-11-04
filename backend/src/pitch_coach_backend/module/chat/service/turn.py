import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from pitch_coach_backend.module.chat.ai_client import TurnFailure, TurnFinal
from pitch_coach_backend.module.chat.ai_response import StreamLine
from pitch_coach_backend.module.chat.entity import (
    ChatAction,
    ChatConversation,
    ChatDiagnostic,
    ChatMessage,
    ChatRun,
)
from pitch_coach_backend.module.chat.repository import ChatRepository
from pitch_coach_backend.module.chat.service.goal_check import settle_goal_check
from pitch_coach_backend.module.chat.service.practice import start_practice
from pitch_coach_backend.module.chat.service.run import (
    ClaimedRun,
    add_run_log,
    cancel,
    claimed,
    digest,
    lock_current,
    lock_or_cancel,
)


class ScopeChangeRejected(Exception):
    pass


def mark_sending(
    db: Session, run: ClaimedRun, turn_call: int, request_id: str
) -> ClaimedRun | None:
    row = lock_current(db, run)
    if row is None:
        return None
    row.turn_calls = [
        *(row.turn_calls or []),
        {
            "turn_call": turn_call,
            "request_id": request_id,
            "state_revision": row.state_revision if turn_call > 1 else None,
            "stage": "SENDING",
        },
    ]
    row.row_revision += 1
    db.commit()
    return claimed(row)


def save_progress(db: Session, run: ClaimedRun, turn_call: int, line: StreamLine) -> bool:
    row = lock_current(db, run)
    if row is None:
        return False
    row.progress_turn_call = turn_call
    row.progress_seq = line.seq
    if line.event == "plan":
        row.plan = line.data.get("plan")
    else:
        row.trace = [*(row.trace or []), line.data.get("trace")]
    db.commit()
    return True


def wait_for_action(
    db: Session, run: ClaimedRun, turn_call: int, final: TurnFinal
) -> ClaimedRun | None:
    data = final.data
    lock_or_cancel(db, run, *_answer_sources(data))
    repo = ChatRepository(db)
    row = lock_current(db, run)
    if row is None:
        return None
    conversation = repo.lock_conversation(row.conversation_id)
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


def finish(db: Session, run: ClaimedRun, turn_call: int, final: TurnFinal) -> bool:
    data = final.data
    pitch_ids, take_ids = _answer_sources(data)
    if run.first_trigger == "TAKE_REVIEWED":
        take_ids.add(data["goal_result"]["take_id"])
    lock_or_cancel(db, run, pitch_ids, take_ids)
    repo = ChatRepository(db)
    row = lock_current(db, run)
    if row is None:
        return False
    conversation = repo.lock_conversation(row.conversation_id)
    if row.first_trigger == "TAKE_REVIEWED" and not settle_goal_check(
        repo, row, conversation, data["goal_result"]
    ):
        cancel(db, run)
        return False
    _apply_scope(row, conversation, data)
    start_practice(db, repo, row, data)
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
    row = lock_current(db, run)
    if row is None:
        return False
    now = datetime.now(UTC)
    row.status = "FAILED"
    row.error_code = failure.code
    row.retry_after_s = failure.retry_after_s
    row.finished_at = now
    row.row_revision += 1
    add_run_log(repo, row, now)
    if row.turn_calls:
        _add_diagnostics(repo, row, row.turn_calls[-1]["turn_call"], failure.diagnostics, now)
    db.commit()
    return True


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
