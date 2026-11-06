import uuid
from typing import Any

from sqlalchemy.orm import Session

from pitch_coach_backend.module.chat.ai_response import FetchPitchArgs, FetchTakeArgs
from pitch_coach_backend.module.chat.dto import PITCH_SUMMARIES, ActionResultDTO
from pitch_coach_backend.module.chat.entity import ChatAction, ChatRun
from pitch_coach_backend.module.chat.repository import ChatRepository
from pitch_coach_backend.module.chat.service.owned import owned_pitch, owned_take_in
from pitch_coach_backend.module.chat.service.request import (
    pitch_context,
    pitch_summary,
    run_scope,
    take_snapshot,
)
from pitch_coach_backend.module.chat.service.result import ambiguous, failed, succeeded


def fetch_take(
    db: Session, row: ChatRun, saved: ChatAction, action: dict[str, Any], args: FetchTakeArgs
) -> ActionResultDTO:
    repo = ChatRepository(db)
    conversation = repo.get_conversation(row.conversation_id)
    take = owned_take_in(db, row.user_id, args.pitch_id, args.take_id)
    if take is None or str(run_scope(row, conversation)) != args.pitch_id:
        return failed(action, "NOT_FOUND", "기록을 찾을 수 없어요.")
    _remember_take(row, saved, take.Take.id)
    return succeeded(action, {"take": take_snapshot(repo, take)})


def _remember_take(row: ChatRun, saved: ChatAction, take_id: uuid.UUID) -> None:
    sources = row.read_sources or {"pitches": [], "takes": []}
    if str(take_id) not in sources["takes"]:
        row.read_sources = {**sources, "takes": [*sources["takes"], str(take_id)]}
    saved.source_take_ids = [str(take_id)]


def fetch_pitch(
    db: Session, row: ChatRun, saved: ChatAction, action: dict[str, Any], args: FetchPitchArgs
) -> ActionResultDTO:
    repo = ChatRepository(db)
    if args.pitch_id is not None:
        pitch = owned_pitch(db, row.user_id, args.pitch_id)
        pitch_ids = [pitch.id] if pitch else []
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


def _remember_pitch(row: ChatRun, pitch_id: uuid.UUID) -> None:
    sources = row.read_sources or {"pitches": [], "takes": []}
    if str(pitch_id) not in sources["pitches"]:
        row.read_sources = {**sources, "pitches": [*sources["pitches"], str(pitch_id)]}
