import json
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Row
from sqlalchemy.orm import Session

from pitch_coach_backend.module.chat.dto import (
    AgentChoiceDTO,
    AgentContextDTO,
    AgentHistoryDTO,
    AgentHistoryMessageDTO,
    AgentPageDTO,
    AgentResumeDTO,
    AgentReviewEventDTO,
    AgentRunDTO,
    AgentTurnRequestDTO,
    BudgetUsedDTO,
)
from pitch_coach_backend.module.chat.entity import (
    ChatConversation,
    ChatGoal,
    ChatMessage,
    ChatRun,
)
from pitch_coach_backend.module.chat.repository import ChatRepository
from pitch_coach_backend.module.chat.service.run import ClaimedRun
from pitch_coach_backend.module.chat.service.stale import (
    MASKED_TEXT,
    sources_of,
    stale_reasons,
    uses_deleted,
)
from pitch_coach_backend.module.pitch.entity import Pitch
from pitch_coach_backend.module.pitch.repository import PitchRepository
from pitch_coach_backend.module.pitch.service import standard as standard_service
from pitch_coach_backend.module.take.coach_repository import CoachRepository
from pitch_coach_backend.module.take.entity import Mission, Take
from pitch_coach_backend.module.take.repository import TakeRepository

HISTORY_MESSAGES = 20
PRIOR_ASSESSMENTS = 50
HISTORY_TEXT_LIMIT = 4000
PITCH_SUMMARIES = 20
RECENT_TAKES = 10
CONTEXT_LIMIT_BYTES = 524_288
SNAPSHOT_RESULTS = {
    "TAKE_RESULT": "take_result",
    "SCRIPT_COVERAGE": "script_coverage",
    "REVIEW": "review",
}
APP_STARTED_AT = datetime.now(UTC)
PITCH_KEYS = ("pitch_id", "scope_pitch_id")


class PitchNotFound(Exception):
    pass


class ContextTooLarge(Exception):
    pass


class ProposalNotFound(Exception):
    pass


def build_request(
    db: Session,
    run: ClaimedRun,
    resume: AgentResumeDTO | None,
    budget_used: BudgetUsedDTO,
) -> AgentTurnRequestDTO:
    db.connection(execution_options={"isolation_level": "REPEATABLE READ"})
    repo = ChatRepository(db)
    row = repo.get_run(run.run_id)
    conversation = repo.get_conversation(row.conversation_id)
    first = resume is None
    source = row.input if first else {}
    choice = source.get("choice")
    review_event = source.get("review_event")
    scope_pitch_id = run_scope(row, conversation)
    trigger_message = repo.get_message(row.message_id) if row.message_id else None
    before_seq = trigger_message.seq if trigger_message else None
    context = _fit_context(_context(db, repo, row, conversation, scope_pitch_id, choice))
    return AgentTurnRequestDTO(
        run=AgentRunDTO(
            run_id=row.id,
            conversation_id=row.conversation_id,
            user_id=row.user_id,
            scope_pitch_id=scope_pitch_id,
            trigger=run.first_trigger if first else "ACTION_RESULT",
            state_revision=row.state_revision,
        ),
        user_message=source.get("user_message"),
        choice=AgentChoiceDTO.model_validate(choice) if choice else None,
        review_event=AgentReviewEventDTO.model_validate(review_event) if review_event else None,
        page=AgentPageDTO.model_validate(row.input["page"]),
        history=_history(
            db, repo, row.user_id, conversation.id, conversation.goal_version, before_seq
        ),
        context=context,
        resume=resume,
        budget_used=budget_used,
    )


def offered_sources(request: AgentTurnRequestDTO) -> dict[str, list[str]]:
    pitches: set[str] = set()
    takes: set[str] = set()
    stack: list[Any] = [request.model_dump(mode="json")]
    while stack:
        value = stack.pop()
        if isinstance(value, dict):
            for key, item in value.items():
                if key in PITCH_KEYS and isinstance(item, str):
                    pitches.add(item)
                elif key == "take_id" and isinstance(item, str):
                    takes.add(item)
                else:
                    stack.append(item)
        elif isinstance(value, list):
            stack.extend(value)
    return {"pitches": sorted(pitches), "takes": sorted(takes)}


def run_scope(row: ChatRun, conversation: ChatConversation) -> uuid.UUID | None:
    if row.first_trigger == "TAKE_REVIEWED":
        return row.scope_pitch_id
    return conversation.scope_pitch_id


def _json_size(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def _fit_context(context: AgentContextDTO) -> AgentContextDTO:
    if _json_size(context.model_dump(mode="json")) <= CONTEXT_LIMIT_BYTES:
        return context
    pitch = context.pitch or {}
    focus = pitch.get("focus_take") or {}
    coverage = (focus.get("results") or {}).get("script_coverage")
    if not isinstance(coverage, dict) or "evaluations" not in coverage:
        raise ContextTooLarge()
    trimmed = {key: value for key, value in coverage.items() if key != "evaluations"}
    focus = {
        **focus,
        "results": {**focus["results"], "script_coverage": trimmed},
        "omitted_paths": [*focus["omitted_paths"], "results.script_coverage.evaluations"],
    }
    context = AgentContextDTO.model_validate(
        {**context.model_dump(), "pitch": {**pitch, "focus_take": focus}}
    )
    if _json_size(context.model_dump(mode="json")) > CONTEXT_LIMIT_BYTES:
        raise ContextTooLarge()
    return context


def _history(
    db: Session,
    repo: ChatRepository,
    user_id: uuid.UUID,
    conversation_id: uuid.UUID,
    goal_version: int,
    before_seq: int | None,
) -> AgentHistoryDTO:
    messages = repo.list_recent_messages(
        conversation_id, before_seq=before_seq, limit=HISTORY_MESSAGES
    )
    assessments = repo.list_recent_assessments(
        conversation_id, before_seq=before_seq, limit=PRIOR_ASSESSMENTS
    )
    pitch_ids: set[str] = set()
    take_ids: set[str] = set()
    for message in messages:
        message_pitch_ids, message_take_ids = _message_sources(message)
        pitch_ids.update(message_pitch_ids)
        take_ids.update(message_take_ids)
    for assessment in assessments:
        assessment_pitch_ids, assessment_take_ids = _assessment_sources(assessment)
        pitch_ids.update(assessment_pitch_ids)
        take_ids.update(assessment_take_ids)
    current = current_sources(db, repo, user_id, pitch_ids, take_ids)
    corpus_version = repo.latest_corpus_version(APP_STARTED_AT)
    return AgentHistoryDTO(
        summary=None,
        messages=[
            _history_message(message, current, goal_version, corpus_version) for message in messages
        ],
        prior_assessments=[
            assessment for assessment in assessments if not _assessment_deleted(assessment, current)
        ],
    )


def _history_message(
    message: ChatMessage,
    current: dict[str, Any],
    goal_version: int,
    corpus_version: str | None,
) -> AgentHistoryMessageDTO:
    pitch_ids, take_ids = _message_sources(message)
    if uses_deleted(pitch_ids, take_ids, current):
        return AgentHistoryMessageDTO(
            role=message.role,
            text=MASKED_TEXT,
            truncated=False,
            basis=None,
            stale_reasons=["SOURCE_DELETED"],
        )
    text, truncated = _shorten(message)
    basis = message.basis if isinstance(message.basis, dict) else None
    return AgentHistoryMessageDTO(
        role=message.role,
        text=text,
        truncated=truncated,
        basis=basis,
        stale_reasons=stale_reasons(
            basis, message.created_at, current, goal_version, corpus_version
        ),
    )


def _message_sources(message: ChatMessage) -> tuple[set[str], set[str]]:
    return set(message.source_pitch_ids or []), set(message.source_take_ids or [])


def _assessment_sources(assessment: dict[str, Any]) -> tuple[set[str], set[str]]:
    evidence_basis = assessment.get("evidence_basis")
    records = evidence_basis.get("records") if isinstance(evidence_basis, dict) else None
    return sources_of(records)


def _assessment_deleted(assessment: dict[str, Any], current: dict[str, Any]) -> bool:
    pitch_ids, take_ids = _assessment_sources(assessment)
    return uses_deleted(pitch_ids, take_ids, current)


def current_sources(
    db: Session,
    repo: ChatRepository,
    user_id: uuid.UUID,
    pitch_ids: set[str],
    take_ids: set[str],
) -> dict[str, Any]:
    pitch_repository = PitchRepository(db)
    return {
        "pitches": {
            pitch_id: _current_pitch(db, repo, pitch_repository, user_id, pitch_id)
            for pitch_id in pitch_ids
        },
        "takes": {
            take_id: _current_take(repo, pitch_repository, user_id, take_id) for take_id in take_ids
        },
    }


def _current_pitch(
    db: Session,
    repo: ChatRepository,
    pitch_repository: PitchRepository,
    user_id: uuid.UUID,
    pitch_id: Any,
) -> dict[str, Any] | None:
    parsed = _as_uuid(pitch_id)
    pitch = pitch_repository.get_owned(parsed, user_id) if parsed else None
    if pitch is None:
        return None
    standard_version = standard_service.latest_analysis_version(db, pitch.id)
    return {
        **_pitch_versions(db, pitch.id, standard_version),
        "last_completed_at": repo.last_completed_at(pitch.id),
    }


def _current_take(
    repo: ChatRepository, pitch_repository: PitchRepository, user_id: uuid.UUID, take_id: Any
) -> dict[str, Any] | None:
    parsed = _as_uuid(take_id)
    row = repo.get_take_with_review(parsed) if parsed else None
    if row is None or pitch_repository.get_owned(row.Take.pitch_id, user_id) is None:
        return None
    results = repo.list_ai_results(row.Take.id, list(SNAPSHOT_RESULTS))
    take_result = next((saved for saved in results if saved.kind == "TAKE_RESULT"), None)
    criteria = (take_result.payload or {}).get("criteria") or {} if take_result else {}
    return {
        "results": {saved.kind: saved.revision for saved in results},
        "rule_versions": {
            area: (rule or {}).get("criteria_version") for area, rule in criteria.items()
        },
    }


def _as_uuid(value: Any) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value))
    except ValueError:
        return None


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
    scope_pitch_id: uuid.UUID | None,
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
        pitches=[pitch_summary(pitch, reviews.get(pitch.pitch_id)) for pitch in pitches],
        pitches_total=repo.count_pitches(row.user_id),
        pitch=(
            pitch_context(
                db,
                row.user_id,
                scope_pitch_id,
                uuid.UUID(page_take_id) if page_take_id else None,
            )
            if scope_pitch_id
            else None
        ),
        conversation_goal=goal_view(repo.get_goal(conversation.id, conversation.goal_version)),
        goal_version=conversation.goal_version,
        loaded_takes=loaded_takes,
        removed_sources=removed_sources,
        selected_proposal=selected_proposal(repo, conversation.id, choice),
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
            loaded.append(take_snapshot(repo, take))
    return loaded, removed


def pitch_context(
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
    return take_snapshot(repo, row) if row else None


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


def pitch_summary(pitch: Row, review: Row | None) -> dict[str, Any]:
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


def take_snapshot(repo: ChatRepository, row: Row) -> dict[str, Any]:
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
        "completed_fully": completed_fully(take, row.page_count, slides),
        "script_version_id": str(take.script_version_id),
        "presentation_version_id": str(take.presentation_version_id),
        "standard_version": take.standard_version,
        "completed_at": take.completed_at.isoformat() if take.completed_at else None,
        "review": _review_summary(row.review_revision, row.review_payload),
    }


def completed_fully(take: Take, page_count: int | None, slides: set[int]) -> bool | None:
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


def goal_view(goal: ChatGoal | None) -> dict[str, Any] | None:
    if goal is None or goal.goal is None:
        return None
    return {
        **goal.goal,
        "version": goal.version,
        "status": goal.status,
        "created_at": goal.created_at.isoformat(),
    }


def selected_proposal(
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
