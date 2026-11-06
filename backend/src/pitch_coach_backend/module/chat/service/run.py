import hashlib
import json
import os
import socket
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from pitch_coach_backend.module.chat.dto import AgentTrigger
from pitch_coach_backend.module.chat.entity import ChatConversation, ChatRun, ChatRunLog
from pitch_coach_backend.module.chat.repository import ChatRepository
from pitch_coach_backend.module.chat.service.owned import parse_uuid

SEOUL = ZoneInfo("Asia/Seoul")
OWNER = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"
LEASE = timedelta(seconds=30)


def seoul_day(at: datetime | None = None) -> date:
    """하루 한도를 세는 서울 날짜. 시각을 주지 않으면 지금이다."""
    return (datetime.now(UTC) if at is None else at).astimezone(SEOUL).date()


class RunLost(Exception):
    pass


class InvalidSource(Exception):
    pass


@dataclass(frozen=True)
class ClaimedRun:
    run_id: uuid.UUID
    first_trigger: AgentTrigger
    row_revision: int
    deadline: datetime


def claimed(row: ChatRun) -> ClaimedRun:
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
    take_lease(row)
    row.row_revision += 1
    db.commit()
    return claimed(row)


def take_lease(row: ChatRun) -> None:
    row.lease_owner = OWNER
    row.lease_until = datetime.now(UTC) + LEASE


def renew_lease(db: Session, run_id: uuid.UUID) -> bool:
    renewed = ChatRepository(db).renew_run_lease(run_id, OWNER, datetime.now(UTC) + LEASE)
    db.commit()
    return renewed


def lock_current(db: Session, run: ClaimedRun) -> ChatRun | None:
    return ChatRepository(db).lock_run(
        run.run_id, statuses=("RUNNING",), row_revision=run.row_revision
    )


def lock_conversation_of(db: Session, run: ClaimedRun) -> ChatConversation | None:
    repo = ChatRepository(db)
    row = repo.get_run(run.run_id)
    return repo.lock_conversation(row.conversation_id) if row is not None else None


def _parse_ids(values: Iterable[Any]) -> tuple[set[uuid.UUID], bool]:
    parsed: set[uuid.UUID] = set()
    valid = True
    for value in values:
        if value is None:
            continue
        if (parsed_id := parse_uuid(value)) is None:
            valid = False
        else:
            parsed.add(parsed_id)
    return parsed, valid


def lock_ids(db: Session, pitch_ids: set[str], take_ids: set[str]) -> bool:
    pitches, pitches_valid = _parse_ids(pitch_ids)
    takes, takes_valid = _parse_ids(take_ids)
    repo = ChatRepository(db)
    locked = all(repo.lock_pitch_id(pitch_id) for pitch_id in sorted(pitches))
    locked = all(repo.lock_take_id(take_id) for take_id in sorted(takes)) and locked
    return locked and pitches_valid and takes_valid


def check_sources(db: Session, run: ClaimedRun, pitch_ids: set[str], take_ids: set[str]) -> None:
    pitches, pitches_valid = _parse_ids(pitch_ids)
    takes, takes_valid = _parse_ids(take_ids)
    repo = ChatRepository(db)
    row = repo.get_run(run.run_id)
    offered_pitches, offered_takes = _offered(row)
    if (
        not (pitches_valid and takes_valid)
        or not pitch_ids <= offered_pitches
        or not take_ids <= offered_takes
        or repo.foreign_sources(row.user_id, pitches, takes)
    ):
        raise InvalidSource(run.run_id)


def _offered(row: ChatRun) -> tuple[set[str], set[str]]:
    sent = [call.get("sources", {}) for call in row.turn_calls or []]
    pitches = {pitch_id for sources in sent for pitch_id in sources.get("pitches", [])}
    takes = {take_id for sources in sent for take_id in sources.get("takes", [])}
    return pitches, takes


def lock_or_cancel(db: Session, run: ClaimedRun, pitch_ids: set[str], take_ids: set[str]) -> None:
    if lock_ids(db, pitch_ids, take_ids):
        return
    db.rollback()
    cancel(db, run)
    raise RunLost(run.run_id)


def cancel(db: Session, run: ClaimedRun) -> None:
    row = lock_current(db, run)
    if row is None:
        return
    now = datetime.now(UTC)
    release_unsent(ChatRepository(db), row)
    row.status = "CANCELLED"
    row.state = None
    row.error_code = "AGENT_CANCELLED"
    row.finished_at = now
    row.row_revision += 1
    add_run_log(ChatRepository(db), row, now)
    db.commit()


def release_unsent(repo: ChatRepository, row: ChatRun) -> None:
    if not row.turn_calls:
        repo.release_service_turn(row.accepted_at.astimezone(SEOUL).date())


def add_run_log(repo: ChatRepository, row: ChatRun, now: datetime) -> None:
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


def digest(value: Any) -> str:
    canonical = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
