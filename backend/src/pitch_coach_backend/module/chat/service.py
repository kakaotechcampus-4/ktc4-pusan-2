import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import Row
from sqlalchemy.orm import Session

from pitch_coach_backend.module.chat.ai_client import TurnFailure, TurnFinal
from pitch_coach_backend.module.chat.ai_response import StreamLine
from pitch_coach_backend.module.chat.dto import (
    ActionResultDTO,
    AgentChoiceDTO,
    AgentContextDTO,
    AgentErrorDTO,
    AgentHistoryDTO,
    AgentHistoryMessageDTO,
    AgentPageDTO,
    AgentResumeDTO,
    AgentRunDTO,
    AgentTrigger,
    AgentTurnRequestDTO,
    BudgetUsedDTO,
)
from pitch_coach_backend.module.chat.entity import (
    ChatAction,
    ChatConversation,
    ChatDiagnostic,
    ChatGoal,
    ChatMessage,
    ChatRun,
    ChatRunLog,
)
from pitch_coach_backend.module.chat.repository import ChatRepository

SEOUL = ZoneInfo("Asia/Seoul")
HISTORY_MESSAGES = 20
PRIOR_ASSESSMENTS = 50
HISTORY_TEXT_LIMIT = 4000
PITCH_SUMMARIES = 20


class ScopeNotSupported(Exception):
    pass


class ProposalNotFound(Exception):
    pass


class RunLost(Exception):
    pass


@dataclass(frozen=True)
class ClaimedRun:
    run_id: uuid.UUID
    first_trigger: AgentTrigger
    row_revision: int
    deadline: datetime


def _claimed(row: ChatRun) -> ClaimedRun:
    return ClaimedRun(
        run_id=row.id,
        first_trigger=row.first_trigger,
        row_revision=row.row_revision,
        deadline=row.deadline_at,
    )


def claim(db: Session, run_id: uuid.UUID) -> ClaimedRun | None:
    row = ChatRepository(db).lock_run(run_id, statuses=("QUEUED",))
    if row is None:
        return None
    row.status = "RUNNING"
    row.row_revision += 1
    db.commit()
    return _claimed(row)


def build_request(
    db: Session,
    run: ClaimedRun,
    resume: AgentResumeDTO | None,
    budget_used: BudgetUsedDTO,
) -> AgentTurnRequestDTO:
    repo = ChatRepository(db)
    row = repo.get_run(run.run_id)
    conversation = repo.get_conversation(row.conversation_id)
    if conversation.scope_pitch_id is not None:
        raise ScopeNotSupported(conversation.id)
    first = resume is None
    source = row.input if first else {}
    choice = source.get("choice")
    trigger_message = repo.get_message(row.message_id) if row.message_id else None
    before_seq = trigger_message.seq if trigger_message else None
    return AgentTurnRequestDTO(
        run=AgentRunDTO(
            run_id=row.id,
            conversation_id=row.conversation_id,
            user_id=row.user_id,
            scope_pitch_id=None,
            trigger=run.first_trigger if first else "ACTION_RESULT",
            state_revision=row.state_revision,
        ),
        user_message=source.get("user_message"),
        choice=AgentChoiceDTO.model_validate(choice) if choice else None,
        review_event=None,
        page=AgentPageDTO.model_validate(row.input["page"]),
        history=_history(repo, conversation.id, before_seq),
        context=_context(repo, row, conversation, choice),
        resume=resume,
        budget_used=budget_used,
    )


def _history(
    repo: ChatRepository, conversation_id: uuid.UUID, before_seq: int | None
) -> AgentHistoryDTO:
    messages = repo.list_recent_messages(
        conversation_id, before_seq=before_seq, limit=HISTORY_MESSAGES
    )
    return AgentHistoryDTO(
        summary=None,
        messages=[_history_message(message) for message in messages],
        prior_assessments=repo.list_recent_assessments(
            conversation_id, before_seq=before_seq, limit=PRIOR_ASSESSMENTS
        ),
    )


def _history_message(message: ChatMessage) -> AgentHistoryMessageDTO:
    text, truncated = _shorten(message)
    return AgentHistoryMessageDTO(
        role=message.role,
        text=text,
        truncated=truncated,
        basis=message.basis if isinstance(message.basis, dict) else None,
        stale_reasons=[],
    )


def _shorten(message: ChatMessage) -> tuple[str, bool]:
    if len(message.text) <= HISTORY_TEXT_LIMIT:
        return message.text, False
    kept: list[str] = []
    size = 0
    for claim_item in message.claims or []:
        text = claim_item.get("text", "")
        added = len(text) + (1 if kept else 0)
        if size + added > HISTORY_TEXT_LIMIT:
            break
        kept.append(text)
        size += added
    return "\n".join(kept), True


def _context(
    repo: ChatRepository,
    row: ChatRun,
    conversation: ChatConversation,
    choice: dict[str, Any] | None,
) -> AgentContextDTO:
    pitches = repo.list_pitch_summaries(row.user_id, limit=PITCH_SUMMARIES)
    reviews = {
        review.pitch_id: review
        for review in repo.list_last_reviews([pitch.pitch_id for pitch in pitches])
    }
    return AgentContextDTO(
        pitches=[_pitch_summary(pitch, reviews.get(pitch.pitch_id)) for pitch in pitches],
        pitches_total=repo.count_pitches(row.user_id),
        pitch=None,
        conversation_goal=_goal(repo.get_goal(conversation.id, conversation.goal_version)),
        goal_version=conversation.goal_version,
        loaded_takes=[],
        removed_sources=[],
        selected_proposal=_selected_proposal(repo, conversation.id, choice),
    )


def _pitch_summary(pitch: Row, review: Row | None) -> dict[str, Any]:
    return {
        "pitch_id": str(pitch.pitch_id),
        "title": pitch.title,
        "take_count": pitch.take_count,
        "last_practiced_at": (
            pitch.last_practiced_at.isoformat() if pitch.last_practiced_at else None
        ),
        "last_review": _last_review(review) if review else None,
    }


def _last_review(review: Row) -> dict[str, Any]:
    payload = review.payload or {}
    dimensions = payload.get("dimensions") or {}
    return {
        "take_id": str(review.take_id),
        "take_number": review.take_number,
        "score": payload.get("score"),
        "priority_dimensions": [name for name, state in dimensions.items() if state == "PRIORITY"],
    }


def _goal(goal: ChatGoal | None) -> dict[str, Any] | None:
    if goal is None or goal.goal is None:
        return None
    return {
        **goal.goal,
        "version": goal.version,
        "status": goal.status,
        "created_at": goal.created_at.isoformat(),
    }


def _selected_proposal(
    repo: ChatRepository, conversation_id: uuid.UUID, choice: dict[str, Any] | None
) -> dict[str, Any] | None:
    if choice is None:
        return None
    card = repo.find_proposal_card(conversation_id, choice["proposal_id"])
    option = next(
        (item for item in (card or {}).get("options", []) if item.get("label") == choice["option"]),
        None,
    )
    if option is None:
        raise ProposalNotFound(choice["proposal_id"])
    return {
        "proposal_id": card["proposal_id"],
        "option": choice["option"],
        "goal": option["goal"],
        "content_hash": option["content_hash"],
        "evidence_basis": card["evidence_basis"],
    }


def _lock_current(db: Session, run: ClaimedRun) -> ChatRun | None:
    return ChatRepository(db).lock_run(
        run.run_id, statuses=("RUNNING",), row_revision=run.row_revision
    )


def mark_sending(
    db: Session, run: ClaimedRun, turn_call: int, request_id: str
) -> ClaimedRun | None:
    row = _lock_current(db, run)
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
    return _claimed(row)


def save_progress(db: Session, run: ClaimedRun, turn_call: int, line: StreamLine) -> bool:
    row = _lock_current(db, run)
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
    repo = ChatRepository(db)
    row = _lock_current(db, run)
    if row is None:
        return None
    data = final.data
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
            args_digest=_digest(action["args"]),
            autonomy=action["autonomy"],
            idempotency_key=action["idempotency_key"],
            args_hash=action["args_hash"],
            status="PENDING",
        )
    )
    _add_diagnostics(repo, row, turn_call, final.diagnostics, datetime.now(UTC))
    row.row_revision += 1
    db.commit()
    return _claimed(row)


def execute_action(db: Session, run: ClaimedRun, action: dict[str, Any]) -> ActionResultDTO:
    row = _lock_current(db, run)
    if row is None:
        raise RunLost(run.run_id)
    result = ActionResultDTO(
        action_id=action["action_id"],
        type=action["type"],
        status="FAILED",
        data=None,
        error=AgentErrorDTO(code="VALIDATION_ERROR", message="아직 지원하지 않는 행동이에요."),
    )
    saved = ChatRepository(db).get_action(row.id, action["action_id"])
    saved.status = "FAILED"
    saved.result = result.model_dump(mode="json")
    db.commit()
    return result


def _digest(value: Any) -> str:
    canonical = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def finish(db: Session, run: ClaimedRun, turn_call: int, final: TurnFinal) -> bool:
    repo = ChatRepository(db)
    row = _lock_current(db, run)
    if row is None:
        return False
    conversation = repo.lock_conversation(row.conversation_id)
    data = final.data
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
    _add_run_log(repo, row, now)
    _add_diagnostics(repo, row, turn_call, final.diagnostics, now)
    db.commit()
    return True


def fail(db: Session, run: ClaimedRun, failure: TurnFailure) -> bool:
    repo = ChatRepository(db)
    row = _lock_current(db, run)
    if row is None:
        return False
    now = datetime.now(UTC)
    row.status = "FAILED"
    row.error_code = failure.code
    row.retry_after_s = failure.retry_after_s
    row.finished_at = now
    row.row_revision += 1
    _add_run_log(repo, row, now)
    if row.turn_calls:
        _add_diagnostics(repo, row, row.turn_calls[-1]["turn_call"], failure.diagnostics, now)
    db.commit()
    return True


def _add_run_log(repo: ChatRepository, row: ChatRun, now: datetime) -> None:
    usage = row.usage or {}
    repo.add(
        ChatRunLog(
            run_id=row.id,
            day=now.astimezone(SEOUL).date(),
            user_id=row.user_id,
            status=row.status,
            ms=round((now - row.accepted_at).total_seconds() * 1000),
            input_tokens=usage.get("input_tokens"),
            output_tokens=usage.get("output_tokens"),
        )
    )


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
