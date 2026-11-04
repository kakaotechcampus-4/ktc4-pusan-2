import uuid
from typing import Any

from sqlalchemy import Row
from sqlalchemy.orm import Session

from pitch_coach_backend.module.chat.ai_response import FetchPitchArgs, FetchTakeArgs
from pitch_coach_backend.module.chat.dto import ActionResultDTO
from pitch_coach_backend.module.chat.entity import ChatRun
from pitch_coach_backend.module.chat.repository import ChatRepository
from pitch_coach_backend.module.chat.service.request import (
    PITCH_SUMMARIES,
    pitch_context,
    pitch_summary,
    run_scope,
    take_snapshot,
)
from pitch_coach_backend.module.chat.service.result import ambiguous, failed, succeeded
from pitch_coach_backend.module.pitch.repository import PitchRepository


def fetch_take(
    db: Session, row: ChatRun, action: dict[str, Any], args: FetchTakeArgs
) -> ActionResultDTO:
    repo = ChatRepository(db)
    conversation = repo.get_conversation(row.conversation_id)
    take = owned_take(db, repo, row.user_id, args.pitch_id, args.take_id)
    if take is None or str(run_scope(row, conversation)) != args.pitch_id:
        return failed(action, "NOT_FOUND", "기록을 찾을 수 없어요.")
    _remember_take(repo, row, action, take.Take.id)
    return succeeded(action, {"take": take_snapshot(repo, take)})


def owned_take(
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


def fetch_pitch(
    db: Session, row: ChatRun, action: dict[str, Any], args: FetchPitchArgs
) -> ActionResultDTO:
    repo = ChatRepository(db)
    if args.pitch_id is not None:
        pitch_ids = _owned_pitch_ids(db, row.user_id, args.pitch_id)
    else:
        pitch_ids = repo.find_pitch_ids_by_name(row.user_id, args.name.strip())
    if not pitch_ids:
        return failed(action, "NOT_FOUND", "발표를 찾을 수 없어요.")
    if len(pitch_ids) > 1:
        rows = repo.list_pitch_summaries(row.user_id, limit=PITCH_SUMMARIES, pitch_ids=pitch_ids)
        reviews = {review.pitch_id: review for review in repo.list_last_reviews(pitch_ids)}
        candidates = [pitch_summary(pitch, reviews.get(pitch.pitch_id)) for pitch in rows]
        return ambiguous(action, {"candidates": candidates})
    pitch_id = pitch_ids[0]
    _remember_pitch(row, pitch_id)
    return succeeded(action, {"pitch": pitch_context(db, row.user_id, pitch_id, None)})


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
