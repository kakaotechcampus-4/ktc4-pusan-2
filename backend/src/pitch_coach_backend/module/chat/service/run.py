import hashlib
import json
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from pitch_coach_backend.module.chat.dto import AgentTrigger
from pitch_coach_backend.module.chat.entity import ChatRun, ChatRunLog
from pitch_coach_backend.module.chat.repository import ChatRepository

SEOUL = ZoneInfo("Asia/Seoul")


class RunLost(Exception):
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
    row.row_revision += 1
    db.commit()
    return claimed(row)


def lock_current(db: Session, run: ClaimedRun) -> ChatRun | None:
    return ChatRepository(db).lock_run(
        run.run_id, statuses=("RUNNING",), row_revision=run.row_revision
    )


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


def lock_ids(db: Session, pitch_ids: set[str], take_ids: set[str]) -> bool:
    pitches, pitches_valid = _parse_ids(pitch_ids)
    takes, takes_valid = _parse_ids(take_ids)
    repo = ChatRepository(db)
    locked = all(repo.lock_pitch_id(pitch_id) for pitch_id in sorted(pitches))
    locked = all(repo.lock_take_id(take_id) for take_id in sorted(takes)) and locked
    return locked and pitches_valid and takes_valid


def lock_or_cancel(db: Session, run: ClaimedRun, pitch_ids: set[str], take_ids: set[str]) -> None:
    if lock_ids(db, pitch_ids, take_ids):
        return
    db.rollback()
    _cancel(db, run)
    raise RunLost(run.run_id)


def _cancel(db: Session, run: ClaimedRun) -> None:
    row = lock_current(db, run)
    if row is None:
        return
    now = datetime.now(UTC)
    row.status = "CANCELLED"
    row.state = None
    row.error_code = "AGENT_CANCELLED"
    row.finished_at = now
    row.row_revision += 1
    add_run_log(ChatRepository(db), row, now)
    db.commit()


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
