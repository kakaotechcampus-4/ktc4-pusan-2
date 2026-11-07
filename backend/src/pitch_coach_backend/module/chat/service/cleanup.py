import uuid
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from pitch_coach_backend.module.chat.repository import ChatRepository
from pitch_coach_backend.module.chat.service.goal_check import deactivate_goals_without_base
from pitch_coach_backend.module.chat.service.run import SEOUL
from pitch_coach_backend.module.chat.service.stale import MASKED_TEXT

CLEANUP_LEASE = timedelta(hours=1)
ALERT_AFTER = timedelta(hours=2)
CONVERSATION_RETENTION = timedelta(days=180)
RUN_RETENTION = timedelta(days=7)
RUN_LOG_RETENTION = timedelta(days=180)
PROFILE_ITEM_RETENTION = timedelta(days=180)
DELETE_BATCH = 500


def run_cleanup(db: Session, owner: str, now: datetime) -> tuple[int, int] | None:
    repo = ChatRepository(db)
    lease = repo.take_cleanup(owner, now + CLEANUP_LEASE, now)
    if lease is None:
        db.rollback()
        return None
    token = lease.fencing_token
    db.commit()
    cleaned = clean_deleted_sources(db)
    expired = delete_expired(db, now)
    lease = repo.get_cleanup_lease()
    if lease is not None and lease.fencing_token == token:
        lease.lease_until = now
        db.commit()
    return cleaned, expired


def delete_expired(db: Session, now: datetime) -> int:
    repo = ChatRepository(db)
    deleted = _in_batches(
        db, lambda: repo.delete_inactive_conversations(now - CONVERSATION_RETENTION, DELETE_BATCH)
    )
    deleted += _in_batches(db, lambda: repo.delete_finished_runs(now - RUN_RETENTION, DELETE_BATCH))
    deleted += repo.delete_run_logs_before((now - RUN_LOG_RETENTION).astimezone(SEOUL).date())
    deleted += repo.delete_unused_profile_items(now - PROFILE_ITEM_RETENTION)
    db.commit()
    return deleted


def _in_batches(db: Session, delete_batch: Callable[[], int]) -> int:
    total = 0
    while True:
        count = delete_batch()
        db.commit()
        total += count
        if count < DELETE_BATCH:
            return total


def cleanup_delayed(db: Session, now: datetime) -> bool:
    lease = ChatRepository(db).get_cleanup_lease()
    return lease is not None and lease.updated_at < now - ALERT_AFTER


def clean_deleted_sources(db: Session) -> int:
    deactivate_goals_without_base(db)
    repo = ChatRepository(db)
    cleaned = _mask_answers(repo) + _mask_runs(repo) + _clear_goals(repo)
    db.commit()
    return cleaned


def _mask_answers(repo: ChatRepository) -> int:
    messages = repo.list_unmasked_answers(MASKED_TEXT)
    deleted = _deleted_ids(
        repo,
        {pitch_id for message in messages for pitch_id in message.source_pitch_ids or []},
        {take_id for message in messages for take_id in message.source_take_ids or []},
    )
    masked = 0
    for message in messages:
        if deleted & {*(message.source_pitch_ids or []), *(message.source_take_ids or [])}:
            message.text = MASKED_TEXT
            message.claims = [{"kind": "SYSTEM", "text": MASKED_TEXT}]
            message.cards = None
            message.plan = None
            message.trace = None
            message.evidence = None
            message.basis = None
            message.assessments = None
            message.sources = None
            masked += 1
    return masked


def _mask_runs(repo: ChatRepository) -> int:
    masked = 0
    for run in repo.list_finished_runs_with_content():
        actions = repo.list_actions(run.id)
        read = run.read_sources or {}
        take_ids = {*read.get("takes", []), *(t for a in actions for t in a.source_take_ids or [])}
        if not _deleted_ids(repo, set(read.get("pitches", [])), take_ids):
            continue
        run.state = None
        run.interim_message = None
        run.plan = None
        run.trace = None
        repo.delete_run_events(run.id)
        for action in actions:
            action.result = None
        masked += 1
    return masked


def _clear_goals(repo: ChatRepository) -> int:
    goals = repo.list_goals_with_base()
    existing = repo.existing_take_ids({goal.base_take_id for goal in goals})
    cleared = 0
    for goal in goals:
        if goal.base_take_id not in existing:
            goal.goal = None
            goal.status = None
            cleared += 1
    return cleared


def _deleted_ids(repo: ChatRepository, pitch_ids: set[Any], take_ids: set[Any]) -> set[str]:
    pitches = _parse(pitch_ids)
    takes = _parse(take_ids)
    deleted = {str(value) for value in pitch_ids | take_ids if _parse({value}) == set()}
    deleted |= {str(p) for p in pitches - repo.existing_pitch_ids(pitches)} if pitches else set()
    deleted |= {str(t) for t in takes - repo.existing_take_ids(takes)} if takes else set()
    return deleted


def _parse(values: set[Any]) -> set[uuid.UUID]:
    parsed: set[uuid.UUID] = set()
    for value in values:
        try:
            parsed.add(uuid.UUID(str(value)))
        except ValueError:
            continue
    return parsed
