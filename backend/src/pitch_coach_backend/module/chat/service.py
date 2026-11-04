import asyncio
import copy
import hashlib
import json
import re
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import BaseModel
from sqlalchemy import Row
from sqlalchemy.orm import Session

from pitch_coach_backend.module.chat.ai_client import TurnFailure, TurnFinal, request_review_once
from pitch_coach_backend.module.chat.ai_response import (
    ACTION_ARGS,
    ACTION_AUTONOMY,
    FetchPitchArgs,
    FetchTakeArgs,
    ReassessArgs,
    SaveGoalArgs,
    StartPracticeArgs,
    StreamLine,
)
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
from pitch_coach_backend.module.pitch.entity import Pitch
from pitch_coach_backend.module.pitch.repository import PitchRepository
from pitch_coach_backend.module.pitch.service import standard as standard_service
from pitch_coach_backend.module.take.coach_repository import CoachRepository
from pitch_coach_backend.module.take.dto import UserCriteriaDTO
from pitch_coach_backend.module.take.entity import Mission, Take
from pitch_coach_backend.module.take.exception import NonExistentTake
from pitch_coach_backend.module.take.repository import TakeRepository
from pitch_coach_backend.module.take.service.review import build_review_request

SEOUL = ZoneInfo("Asia/Seoul")
HISTORY_MESSAGES = 20
PRIOR_ASSESSMENTS = 50
HISTORY_TEXT_LIMIT = 4000
PITCH_SUMMARIES = 20
RECENT_TAKES = 10
CONTEXT_LIMIT_BYTES = 524_288


class PitchNotFound(Exception):
    pass


class ContextTooLarge(Exception):
    pass


class ScopeChangeRejected(Exception):
    pass


class ProposalNotFound(Exception):
    pass


class RunLost(Exception):
    pass


type ActionHandler = Callable[[Session, ChatRun, dict[str, Any], BaseModel], ActionResultDTO]

ACTION_HANDLERS: dict[str, ActionHandler] = {}


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
            scope_pitch_id=conversation.scope_pitch_id,
            trigger=run.first_trigger if first else "ACTION_RESULT",
            state_revision=row.state_revision,
        ),
        user_message=source.get("user_message"),
        choice=AgentChoiceDTO.model_validate(choice) if choice else None,
        review_event=None,
        page=AgentPageDTO.model_validate(row.input["page"]),
        history=_history(repo, conversation.id, before_seq),
        context=_context(db, repo, row, conversation, choice),
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
    db: Session,
    repo: ChatRepository,
    row: ChatRun,
    conversation: ChatConversation,
    choice: dict[str, Any] | None,
) -> AgentContextDTO:
    page_take_id = row.input["page"].get("take_id")
    loaded_takes, removed_sources = _read_sources(db, repo, row)
    pitches = repo.list_pitch_summaries(row.user_id, limit=PITCH_SUMMARIES)
    reviews = {
        review.pitch_id: review
        for review in repo.list_last_reviews([pitch.pitch_id for pitch in pitches])
    }
    return AgentContextDTO(
        pitches=[_pitch_summary(pitch, reviews.get(pitch.pitch_id)) for pitch in pitches],
        pitches_total=repo.count_pitches(row.user_id),
        pitch=(
            _pitch_context(
                db,
                row.user_id,
                conversation.scope_pitch_id,
                uuid.UUID(page_take_id) if page_take_id else None,
            )
            if conversation.scope_pitch_id
            else None
        ),
        conversation_goal=_goal(repo.get_goal(conversation.id, conversation.goal_version)),
        goal_version=conversation.goal_version,
        loaded_takes=loaded_takes,
        removed_sources=removed_sources,
        selected_proposal=_selected_proposal(repo, conversation.id, choice),
    )


def _read_sources(
    db: Session, repo: ChatRepository, row: ChatRun
) -> tuple[list[dict[str, Any]], list[uuid.UUID]]:
    sources = row.read_sources or {}
    pitch_repository = PitchRepository(db)
    loaded: list[dict[str, Any]] = []
    removed: list[uuid.UUID] = []
    for pitch_id in map(uuid.UUID, sources.get("pitches", [])):
        if pitch_repository.get_owned(pitch_id, row.user_id) is None:
            removed.append(pitch_id)
    for take_id in map(uuid.UUID, sources.get("takes", [])):
        take = repo.get_take_with_review(take_id)
        if take is None or pitch_repository.get_owned(take.Take.pitch_id, row.user_id) is None:
            removed.append(take_id)
        else:
            loaded.append(_take_snapshot(repo, take))
    return loaded, removed


def _pitch_context(
    db: Session, user_id: uuid.UUID, pitch_id: uuid.UUID, focus_take_id: uuid.UUID | None
) -> dict[str, Any]:
    pitch = PitchRepository(db).get_owned(pitch_id, user_id)
    if pitch is None:
        raise PitchNotFound(pitch_id)
    repo = ChatRepository(db)
    rows = repo.list_recent_takes(pitch.id, limit=RECENT_TAKES)
    slides = repo.slide_numbers([row.Take.id for row in rows])
    standard_version = standard_service.latest_analysis_version(db, pitch.id)
    return {
        "info": _pitch_info(pitch),
        "versions": _pitch_versions(db, pitch.id, standard_version),
        "takes": [_take_summary(row, slides.get(row.Take.id, set())) for row in rows],
        "focus_take": _focus_take(repo, pitch.id, focus_take_id),
        "missions": _pitch_missions(db, repo, pitch.id),
        "criteria": _pitch_criteria(db, pitch.id, standard_version),
    }


def _pitch_info(pitch: Pitch) -> dict[str, Any]:
    min_ms = (pitch.time_limit_sec - pitch.lower_deviation) * 1000
    return {
        "pitch_id": str(pitch.id),
        "title": pitch.title,
        "time_limit_ms": pitch.time_limit_sec * 1000,
        "min_ms": min_ms if min_ms > 0 else None,
        "max_ms": (pitch.time_limit_sec + pitch.upper_deviation) * 1000,
    }


def _pitch_versions(
    db: Session, pitch_id: uuid.UUID, standard_version: int | None
) -> dict[str, Any]:
    pitch_repository = PitchRepository(db)
    script = pitch_repository.get_latest_parsed_script(pitch_id)
    slide_count = pitch_repository.count_slides(script.id) if script else None
    presentation = None
    if slide_count:
        presentation = pitch_repository.get_latest_presentation(pitch_id, page_count=slide_count)
    if presentation is None:
        presentation = pitch_repository.get_latest_presentation(pitch_id)
    return {
        "script_version_id": str(script.id) if script else None,
        "script_version_label": f"V{script.version}" if script else None,
        "presentation_version_id": str(presentation.id) if presentation else None,
        "page_count": presentation.page_count if presentation else None,
        "standard_version": standard_version,
    }


def _focus_take(
    repo: ChatRepository, pitch_id: uuid.UUID, take_id: uuid.UUID | None
) -> dict[str, Any] | None:
    row = repo.get_take_with_review(take_id) if take_id else None
    if row is None or row.Take.pitch_id != pitch_id:
        row = repo.get_latest_completed_take(pitch_id)
    return _take_snapshot(repo, row) if row else None


def _pitch_missions(db: Session, repo: ChatRepository, pitch_id: uuid.UUID) -> list[dict[str, Any]]:
    source = TakeRepository(db).get_latest_reviewed_take_in_pitch(pitch_id)
    if source is None:
        return []
    judged = repo.list_judged_missions(source.id)
    active = CoachRepository(db).active_missions_from(source.id)
    return [_mission(mission) for mission in [*judged, *active]]


def _mission(mission: Mission) -> dict[str, Any]:
    return {
        "mission_id": str(mission.id),
        "source_take_id": str(mission.source_take_id),
        "take_id": str(mission.take_id) if mission.take_id else None,
        "area": mission.area,
        "slide_number": mission.slide_number,
        "target": mission.target,
        "description": mission.description,
        "priority": mission.priority,
        "reason": mission.reason,
        "status": mission.status,
        "observed": mission.observed,
    }


def _pitch_criteria(
    db: Session, pitch_id: uuid.UUID, standard_version: int | None
) -> dict[str, Any] | None:
    criteria = standard_service.criteria_for_version(db, pitch_id, standard_version)
    if criteria is None:
        return None
    return {"standard_version": standard_version, **criteria}


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


SNAPSHOT_RESULTS = {
    "TAKE_RESULT": "take_result",
    "SCRIPT_COVERAGE": "script_coverage",
    "REVIEW": "review",
}


def _take_snapshot(repo: ChatRepository, row: Row) -> dict[str, Any]:
    take = row.Take
    results: dict[str, Any] = dict.fromkeys(SNAPSHOT_RESULTS.values())
    revisions: dict[str, int | None] = dict.fromkeys(SNAPSHOT_RESULTS)
    for saved in repo.list_ai_results(take.id, list(SNAPSHOT_RESULTS)):
        results[SNAPSHOT_RESULTS[saved.kind]] = saved.payload
        revisions[saved.kind] = saved.revision
    slides = repo.slide_numbers([take.id]).get(take.id, set())
    return {
        "summary": _take_summary(row, slides),
        "results": results,
        "revisions": revisions,
        "omitted_paths": [],
    }


def _take_summary(row: Row, slides: set[int]) -> dict[str, Any]:
    take = row.Take
    return {
        "take_id": str(take.id),
        "take_number": take.take_number,
        "status": take.status,
        "mode": take.mode,
        "script_mode": take.script_mode,
        "end_reason": take.end_reason,
        "completed_fully": _completed_fully(take, row.page_count, slides),
        "script_version_id": str(take.script_version_id),
        "presentation_version_id": str(take.presentation_version_id),
        "standard_version": take.standard_version,
        "completed_at": take.completed_at.isoformat() if take.completed_at else None,
        "review": _review_summary(row.review_revision, row.review_payload),
    }


def _completed_fully(take: Take, page_count: int | None, slides: set[int]) -> bool | None:
    if take.end_reason in ("ABANDONED", "CLOSED"):
        return False
    if take.status != "COMPLETED" or take.end_reason is not None:
        return None
    if not page_count or not set(range(1, page_count + 1)) <= slides:
        return None
    return True


def _review_summary(revision: int | None, payload: dict[str, Any] | None) -> dict[str, Any] | None:
    if payload is None:
        return None
    return {
        "revision": revision,
        "score": payload.get("score"),
        "dimensions": payload.get("dimensions"),
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
    data = final.data
    _lock_or_cancel(db, run, *_answer_sources(data))
    repo = ChatRepository(db)
    row = _lock_current(db, run)
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
            args_digest=_digest(action["args"]),
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
    return _claimed(row)


def begin_action(db: Session, run: ClaimedRun, action: dict[str, Any]) -> ActionResultDTO | None:
    row = _lock_current(db, run)
    if row is None:
        raise RunLost(run.run_id)
    saved = ChatRepository(db).get_action(row.id, action["action_id"])
    if saved.status in ("SUCCEEDED", "FAILED"):
        return ActionResultDTO.model_validate(
            {key: value for key, value in saved.result.items() if key != "review_revision"}
        )
    if _invalid(action):
        return _close_action(
            db, row, action, _failed(action, "VALIDATION_ERROR", "실행할 수 없는 행동이에요.")
        )
    saved.status = "RUNNING"
    db.commit()
    return None


def run_action(db: Session, run: ClaimedRun, action: dict[str, Any]) -> ActionResultDTO:
    args = ACTION_ARGS[action["type"]].model_validate(action["args"])
    if isinstance(args, SaveGoalArgs):
        goal = args.goal
        _lock_ids(db, {goal.get("pitch_id")}, {(goal.get("base") or {}).get("take_id")})
    row = _lock_current(db, run)
    if row is None:
        raise RunLost(run.run_id)
    result = ACTION_HANDLERS[action["type"]](db, row, action, args)
    saved = ChatRepository(db).get_action(row.id, action["action_id"])
    saved.status = "SUCCEEDED" if result.status != "FAILED" else "FAILED"
    saved.result = result.model_dump(mode="json")
    db.commit()
    return result


def _invalid(action: dict[str, Any]) -> bool:
    kind = action["type"]
    if kind not in (*ACTION_HANDLERS, "REASSESS") or action["autonomy"] != ACTION_AUTONOMY[kind]:
        return True
    try:
        ACTION_ARGS[kind].model_validate(action["args"])
    except ValueError:
        return True
    return False


def _failed(action: dict[str, Any], code: str, message: str) -> ActionResultDTO:
    return ActionResultDTO(
        action_id=action["action_id"],
        type=action["type"],
        status="FAILED",
        data=None,
        error=AgentErrorDTO(code=code, message=message),
    )


def _succeeded(action: dict[str, Any], data: dict[str, Any]) -> ActionResultDTO:
    return ActionResultDTO(
        action_id=action["action_id"],
        type=action["type"],
        status="SUCCEEDED",
        data=data,
        error=None,
    )


def _fetch_take(
    db: Session, row: ChatRun, action: dict[str, Any], args: FetchTakeArgs
) -> ActionResultDTO:
    repo = ChatRepository(db)
    conversation = repo.get_conversation(row.conversation_id)
    take = _owned_take(db, repo, row.user_id, args.pitch_id, args.take_id)
    if take is None or str(conversation.scope_pitch_id) != args.pitch_id:
        return _failed(action, "NOT_FOUND", "기록을 찾을 수 없어요.")
    _remember_take(repo, row, action, take.Take.id)
    return _succeeded(action, {"take": _take_snapshot(repo, take)})


def _owned_take(
    db: Session, repo: ChatRepository, user_id: uuid.UUID, pitch_id: str, take_id: str
) -> Row | None:
    try:
        pitch_uuid, take_uuid = uuid.UUID(pitch_id), uuid.UUID(take_id)
    except ValueError:
        return None
    if PitchRepository(db).get_owned(pitch_uuid, user_id) is None:
        return None
    take = repo.get_take_with_review(take_uuid)
    if take is None or take.Take.pitch_id != pitch_uuid:
        return None
    return take


def _remember_take(
    repo: ChatRepository, row: ChatRun, action: dict[str, Any], take_id: uuid.UUID
) -> None:
    sources = row.read_sources or {"pitches": [], "takes": []}
    if str(take_id) not in sources["takes"]:
        row.read_sources = {**sources, "takes": [*sources["takes"], str(take_id)]}
    repo.get_action(row.id, action["action_id"]).source_take_ids = [str(take_id)]


def _fetch_pitch(
    db: Session, row: ChatRun, action: dict[str, Any], args: FetchPitchArgs
) -> ActionResultDTO:
    repo = ChatRepository(db)
    if args.pitch_id is not None:
        pitch_ids = _owned_pitch_ids(db, row.user_id, args.pitch_id)
    else:
        pitch_ids = repo.find_pitch_ids_by_name(row.user_id, args.name.strip())
    if not pitch_ids:
        return _failed(action, "NOT_FOUND", "발표를 찾을 수 없어요.")
    if len(pitch_ids) > 1:
        rows = repo.list_pitch_summaries(row.user_id, limit=PITCH_SUMMARIES, pitch_ids=pitch_ids)
        reviews = {review.pitch_id: review for review in repo.list_last_reviews(pitch_ids)}
        candidates = [_pitch_summary(pitch, reviews.get(pitch.pitch_id)) for pitch in rows]
        return _ambiguous(action, {"candidates": candidates})
    pitch_id = pitch_ids[0]
    _remember_pitch(row, pitch_id)
    return _succeeded(action, {"pitch": _pitch_context(db, row.user_id, pitch_id, None)})


def _owned_pitch_ids(db: Session, user_id: uuid.UUID, pitch_id: str) -> list[uuid.UUID]:
    try:
        pitch_uuid = uuid.UUID(pitch_id)
    except ValueError:
        return []
    return [pitch_uuid] if PitchRepository(db).get_owned(pitch_uuid, user_id) else []


def _remember_pitch(row: ChatRun, pitch_id: uuid.UUID) -> None:
    sources = row.read_sources or {"pitches": [], "takes": []}
    if str(pitch_id) not in sources["pitches"]:
        row.read_sources = {**sources, "pitches": [*sources["pitches"], str(pitch_id)]}


def _ambiguous(action: dict[str, Any], data: dict[str, Any]) -> ActionResultDTO:
    return ActionResultDTO(
        action_id=action["action_id"],
        type=action["type"],
        status="AMBIGUOUS",
        data=data,
        error=None,
    )


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


ACTION_HANDLERS["FETCH_TAKE"] = _fetch_take
ACTION_HANDLERS["FETCH_PITCH"] = _fetch_pitch

NEGATIONS = ("하지 마", "말고", "취소", "빼고", "안 해")
CONDITION = re.compile(r"(으)?면(\s|$|,)")
QUOTES = ('"', "'", "“", "”", "‘", "’", "라고")
REQUEST_ENDINGS = re.compile(r"(자|줘|주세요|할래|해 줘|하고 싶어|갈래|해요)(\s|$|[.!?,])")
ACTION_WORDS = {
    "START_PRACTICE": ("연습", "발표", "시작", "가자"),
    "SAVE_GOAL": ("목표", "잡", "정해", "저장"),
}
CLAUSE_SPLIT = re.compile(r"[.!?\n]")


def _authorization(original: str | None, quote: str | None, kind: str) -> str:
    if not original or not quote or quote not in original:
        return "MISSING"
    clause = next((part for part in CLAUSE_SPLIT.split(original) if quote in part), original)
    if (
        any(word in clause for word in NEGATIONS)
        or CONDITION.search(clause)
        or any(mark in clause for mark in QUOTES)
    ):
        return "REJECTED"
    if not any(word in quote for word in ACTION_WORDS[kind]) or not REQUEST_ENDINGS.search(quote):
        return "MISSING"
    return "OK"


def _start_practice(db: Session, repo: ChatRepository, row: ChatRun, data: dict[str, Any]) -> None:
    actions = [action for action in data["actions"] if action["type"] == "START_PRACTICE"]
    if not actions:
        return
    action = actions[0]
    decision = _practice_decision(db, row, action)
    cards = [card for card in data["message"]["cards"] if card.get("type") != "START_PRACTICE"]
    if decision is not None:
        cards.append({"type": "START_PRACTICE", **decision})
        repo.add(
            ChatAction(
                conversation_id=row.conversation_id,
                user_id=row.user_id,
                run_id=row.id,
                action_id=action["action_id"],
                type="START_PRACTICE",
                args=action["args"],
                args_digest=_digest(action["args"]),
                autonomy=action["autonomy"],
                idempotency_key=action["idempotency_key"],
                args_hash=action["args_hash"],
                status="SUCCEEDED",
                result=decision,
            )
        )
    data["message"]["cards"] = cards


def _practice_decision(db: Session, row: ChatRun, action: dict[str, Any]) -> dict[str, Any] | None:
    if row.first_trigger == "TAKE_REVIEWED" or action["autonomy"] != "AUTO_UNDO":
        return None
    try:
        args = StartPracticeArgs.model_validate(action["args"])
        pitch_id = uuid.UUID(args.pitch_id)
    except ValueError:
        return None
    if PitchRepository(db).get_owned(pitch_id, row.user_id) is None:
        return None
    verdict = _authorization(
        row.input.get("user_message"), action.get("authorized_by"), "START_PRACTICE"
    )
    if verdict == "REJECTED":
        return None
    return {"pitch_id": str(pitch_id), "auto": verdict == "OK"}


GOAL_METRICS = {
    "audience_ratio": ("GAZE", "gt_base"),
    "filler_per_min": ("FILLER", "lt_base"),
    "cpm": ("SPEED", "in_range"),
    "duration_ms": ("TIME", "in_range"),
}


class GoalRejected(Exception):
    pass


def _save_goal(
    db: Session, row: ChatRun, action: dict[str, Any], args: SaveGoalArgs
) -> ActionResultDTO:
    repo = ChatRepository(db)
    if row.first_trigger == "TAKE_REVIEWED":
        return _failed(action, "VALIDATION_ERROR", "자동 확인에서는 목표를 저장하지 않아요.")
    if not _goal_authorized(repo, row, action, args.goal):
        return _failed(action, "VALIDATION_ERROR", "목표를 저장해 달라는 요청을 확인하지 못했어요.")
    if repo.count_succeeded_actions(row.id, "SAVE_GOAL") > 0:
        return _failed(action, "VALIDATION_ERROR", "한 번에 목표는 하나만 저장해요.")
    try:
        goal = _rebuild_goal(db, repo, row.user_id, args.goal)
    except GoalRejected:
        return _failed(action, "VALIDATION_ERROR", "기록과 맞지 않는 목표예요.")
    conversation = repo.lock_conversation(row.conversation_id)
    if conversation.goal_version != args.expected_goal_version:
        return _failed(action, "AGENT_REQUEST_CONFLICT", "이미 다른 목표로 바뀌었어요.")
    saved = repo.get_action(row.id, action["action_id"])
    version = conversation.goal_version + 1
    repo.add(
        ChatGoal(
            conversation_id=conversation.id,
            version=version,
            goal=goal,
            status="OPEN",
            base_take_id=uuid.UUID(goal["base"]["take_id"]),
            action_id=saved.id,
        )
    )
    conversation.goal_version = version
    saved.source_take_ids = [goal["base"]["take_id"]]
    db.flush()
    return _succeeded(action, {"goal": _goal(repo.get_goal(conversation.id, version))})


def _goal_authorized(
    repo: ChatRepository, row: ChatRun, action: dict[str, Any], goal: dict[str, Any]
) -> bool:
    choice = row.input.get("choice")
    if choice:
        try:
            proposal = _selected_proposal(repo, row.conversation_id, choice)
        except ProposalNotFound:
            return False
        return proposal["goal"] == goal
    original = row.input.get("user_message")
    return _authorization(original, action.get("authorized_by"), "SAVE_GOAL") == "OK"


def _rebuild_goal(
    db: Session, repo: ChatRepository, user_id: uuid.UUID, goal: dict[str, Any]
) -> dict[str, Any]:
    metric = goal.get("metric")
    if metric not in GOAL_METRICS:
        raise GoalRejected(metric)
    area, operator = GOAL_METRICS[metric]
    slide_number = goal.get("slide_number")
    if goal.get("operator") != operator or (metric == "duration_ms" and slide_number is not None):
        raise GoalRejected(metric)
    base = goal.get("base") or {}
    take = _owned_take(db, repo, user_id, str(goal.get("pitch_id")), str(base.get("take_id")))
    if take is None:
        raise GoalRejected(base.get("take_id"))
    slides = repo.slide_numbers([take.Take.id]).get(take.Take.id, set())
    if _completed_fully(take.Take, take.page_count, slides) is not True:
        raise GoalRejected(base.get("take_id"))
    result = next(iter(repo.list_ai_results(take.Take.id, ["TAKE_RESULT"])), None)
    if result is None or not isinstance(result.payload, dict):
        raise GoalRejected(base.get("take_id"))
    rule_version = ((result.payload.get("criteria") or {}).get(area) or {}).get("criteria_version")
    if not isinstance(rule_version, str):
        raise GoalRejected(area)
    rebuilt = {
        "pitch_id": str(take.Take.pitch_id),
        "metric": metric,
        "slide_number": slide_number,
        "operator": operator,
        "script_version_id": str(take.Take.script_version_id),
        "standard_version": take.Take.standard_version,
        "goal_rule_version": goal.get("goal_rule_version"),
        "base": {
            "take_id": str(take.Take.id),
            "take_number": take.Take.take_number,
            "kind": "TAKE_RESULT",
            "revision": result.revision,
            "value": _metric_value(result.payload, area, metric, slide_number),
            "metric_rule_version": rule_version,
            "range": _metric_range(result.payload, metric),
        },
        "min_delta": goal.get("min_delta"),
    }
    _check_rule(rebuilt)
    if rebuilt != goal:
        raise GoalRejected("MISMATCH")
    return rebuilt


def _metric_value(
    payload: dict[str, Any], area: str, metric: str, slide_number: int | None
) -> float:
    section = (payload.get("areas") or {}).get(area) or {}
    if section.get("unmeasured_reason"):
        raise GoalRejected(area)
    if slide_number is None:
        value = (section.get("take") or {}).get(metric)
    else:
        item = next(
            (s for s in section.get("slides") or [] if s.get("slide_number") == slide_number),
            None,
        )
        if item is None:
            raise GoalRejected(slide_number)
        value = (item.get("metrics") or {}).get(metric)
    if not _finite(value) or value < 0 or (metric == "audience_ratio" and value > 1):
        raise GoalRejected(metric)
    return value


def _metric_range(payload: dict[str, Any], metric: str) -> dict[str, Any] | None:
    if metric == "cpm":
        issues = (((payload.get("criteria") or {}).get("SPEED") or {}).get("issues")) or {}
        low = (issues.get("PACE_SLOW") or {}).get("threshold")
        high = (issues.get("PACE_FAST") or {}).get("threshold")
    elif metric == "duration_ms":
        plan = payload.get("plan") or {}
        low, high = plan.get("min_ms"), plan.get("max_ms")
        if low is None and high is None:
            low = high = plan.get("target_ms")
    else:
        return None
    if low is None and high is None:
        raise GoalRejected("NO_RANGE")
    if any(bound is not None and not _finite(bound) for bound in (low, high)):
        raise GoalRejected("INVALID_DATA")
    if low is not None and high is not None and low > high:
        raise GoalRejected("INVALID_DATA")
    return {"low": low, "high": high}


def _check_rule(goal: dict[str, Any]) -> None:
    rule_version = goal["goal_rule_version"]
    if not isinstance(rule_version, str) or not rule_version:
        raise GoalRejected("goal_rule_version")
    min_delta = goal["min_delta"]
    if goal["operator"] == "in_range":
        if min_delta is not None:
            raise GoalRejected("min_delta")
    elif not _finite(min_delta) or min_delta <= 0:
        raise GoalRejected("min_delta")


def _finite(value: Any) -> bool:
    return (
        isinstance(value, int | float)
        and not isinstance(value, bool)
        and value == value
        and value not in (float("inf"), float("-inf"))
    )


ACTION_HANDLERS["SAVE_GOAL"] = _save_goal

REASSESS_DAILY_LIMIT = 30
EMPTY_CRITERIA = {"display_criteria": [], "targets": [], "script_used": None}
NO_REVIEW = "이 연습은 다시 평가할 자료가 부족해요."


@dataclass(frozen=True)
class PreparedReassess:
    take_id: uuid.UUID
    review_revision: int
    official: dict[str, Any]
    before: dict[str, Any]
    after: dict[str, Any]
    before_digest: str


def prepare_reassess(
    db: Session, run: ClaimedRun, action: dict[str, Any]
) -> PreparedReassess | ActionResultDTO:
    repo = ChatRepository(db)
    row = _lock_current(db, run)
    if row is None:
        raise RunLost(run.run_id)
    args = ReassessArgs.model_validate(action["args"])
    take = _owned_take_by_id(db, repo, row.user_id, args.take_id)
    if take is None:
        return _close_action(
            db, row, action, _failed(action, "NOT_FOUND", "기록을 찾을 수 없어요.")
        )
    review = repo.list_ai_results(take.Take.id, ["REVIEW"])
    if not review:
        return _close_action(db, row, action, _failed(action, "VALIDATION_ERROR", NO_REVIEW))
    if repo.count_succeeded_actions(row.id, "REASSESS") > 0:
        return _close_action(
            db,
            row,
            action,
            _failed(action, "VALIDATION_ERROR", "다시 평가는 한 번에 한 번만 해요."),
        )
    try:
        before = build_review_request(db, take.Take.id).model_dump(mode="json")
        after = _changed_request(before, args.change)
    except ValueError, NonExistentTake:
        return _close_action(db, row, action, _failed(action, "VALIDATION_ERROR", NO_REVIEW))
    if not repo.charge_reassess(row.user_id, datetime.now(SEOUL).date(), REASSESS_DAILY_LIMIT):
        return _close_action(
            db,
            row,
            action,
            _failed(
                action,
                "AGENT_DAILY_LIMIT",
                "오늘은 다시 평가를 더 할 수 없지만 다른 질문은 계속할 수 있어요.",
            ),
        )
    payload = review[0].payload
    db.commit()
    return PreparedReassess(
        take_id=take.Take.id,
        review_revision=review[0].revision,
        official={"score": payload.get("score"), "dimensions": payload.get("dimensions")},
        before=before,
        after=after,
        before_digest=_digest(before),
    )


def _owned_take_by_id(
    db: Session, repo: ChatRepository, user_id: uuid.UUID, take_id: str
) -> Row | None:
    try:
        take_uuid = uuid.UUID(take_id)
    except ValueError:
        return None
    take = repo.get_take_with_review(take_uuid)
    if take is None or PitchRepository(db).get_owned(take.Take.pitch_id, user_id) is None:
        return None
    return take


def _changed_request(before: dict[str, Any], change: dict[str, Any]) -> dict[str, Any]:
    after = copy.deepcopy(before)
    criteria = after["user_criteria"] or copy.deepcopy(EMPTY_CRITERIA)
    criteria.update(change)
    after["user_criteria"] = UserCriteriaDTO.model_validate(criteria).model_dump(mode="json")
    if {**before, "user_criteria": None} != {**after, "user_criteria": None}:
        raise ValueError("허용 칸 밖이 바뀌었다")
    return after


async def call_reassess(prepared: PreparedReassess) -> tuple[dict, dict] | None:
    try:
        async with asyncio.TaskGroup() as group:
            before = group.create_task(request_review_once(prepared.take_id, prepared.before))
            after = group.create_task(request_review_once(prepared.take_id, prepared.after))
    except Exception:
        return None
    return before.result(), after.result()


def finish_reassess(
    db: Session,
    run: ClaimedRun,
    action: dict[str, Any],
    prepared: PreparedReassess,
    replies: tuple[dict, dict] | None,
) -> ActionResultDTO:
    _lock_or_cancel(db, run, set(), {str(prepared.take_id)})
    repo = ChatRepository(db)
    row = _lock_current(db, run)
    if row is None:
        raise RunLost(run.run_id)
    review = repo.list_ai_results(prepared.take_id, ["REVIEW"])
    if (
        replies is None
        or _digest(prepared.before) != prepared.before_digest
        or replies[0]["meta"].get("criteria_versions")
        != replies[1]["meta"].get("criteria_versions")
        or not review
        or review[0].revision != prepared.review_revision
    ):
        result = _failed(
            action, "AGENT_UPSTREAM_ERROR", "다시 평가하지 못했으니 잠시 뒤 다시 시도해 주세요."
        )
    else:
        result = _succeeded(
            action, {"before": replies[0], "after": replies[1], "official": prepared.official}
        )
    saved = repo.get_action(row.id, action["action_id"])
    saved.status = "SUCCEEDED" if result.status == "SUCCEEDED" else "FAILED"
    saved.result = {**result.model_dump(mode="json"), "review_revision": prepared.review_revision}
    saved.source_take_ids = [str(prepared.take_id)]
    db.commit()
    return result


def _close_action(
    db: Session, row: ChatRun, action: dict[str, Any], result: ActionResultDTO
) -> ActionResultDTO:
    saved = ChatRepository(db).get_action(row.id, action["action_id"])
    saved.status = "FAILED"
    saved.result = result.model_dump(mode="json")
    db.commit()
    return result


def _answer_sources(data: dict[str, Any]) -> tuple[set[str], set[str]]:
    pitch_ids: set[str] = set()
    take_ids: set[str] = set()
    for item in data.get("evidence") or []:
        pitch_ids.add(item.get("pitch_id"))
        take_ids.add(item.get("take_id"))
    basis = data.get("basis")
    for record in basis.get("records") or [] if isinstance(basis, dict) else []:
        pitch_ids.add(record.get("pitch_id"))
        take_ids.update(take.get("take_id") for take in record.get("takes") or [])
    pitch_ids.discard(None)
    take_ids.discard(None)
    return pitch_ids, take_ids


def _parse_ids(values: Iterable[Any]) -> tuple[set[uuid.UUID], bool]:
    parsed: set[uuid.UUID] = set()
    valid = True
    for value in values:
        if value is None:
            continue
        try:
            parsed.add(uuid.UUID(str(value)))
        except ValueError:
            valid = False
    return parsed, valid


def _lock_ids(db: Session, pitch_ids: set[str], take_ids: set[str]) -> bool:
    pitches, pitches_valid = _parse_ids(pitch_ids)
    takes, takes_valid = _parse_ids(take_ids)
    repo = ChatRepository(db)
    locked = all(repo.lock_pitch_id(pitch_id) for pitch_id in sorted(pitches))
    locked = all(repo.lock_take_id(take_id) for take_id in sorted(takes)) and locked
    return locked and pitches_valid and takes_valid


def _lock_or_cancel(db: Session, run: ClaimedRun, pitch_ids: set[str], take_ids: set[str]) -> None:
    if _lock_ids(db, pitch_ids, take_ids):
        return
    db.rollback()
    _cancel(db, run)
    raise RunLost(run.run_id)


def _cancel(db: Session, run: ClaimedRun) -> None:
    row = _lock_current(db, run)
    if row is None:
        return
    now = datetime.now(UTC)
    row.status = "CANCELLED"
    row.state = None
    row.error_code = "AGENT_CANCELLED"
    row.finished_at = now
    row.row_revision += 1
    _add_run_log(ChatRepository(db), row, now)
    db.commit()


def _digest(value: Any) -> str:
    canonical = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def finish(db: Session, run: ClaimedRun, turn_call: int, final: TurnFinal) -> bool:
    data = final.data
    pitch_ids, take_ids = _answer_sources(data)
    _lock_or_cancel(db, run, pitch_ids, take_ids)
    repo = ChatRepository(db)
    row = _lock_current(db, run)
    if row is None:
        return False
    conversation = repo.lock_conversation(row.conversation_id)
    _apply_scope(row, conversation, data)
    _start_practice(db, repo, row, data)
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
