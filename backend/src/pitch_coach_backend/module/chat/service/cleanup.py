import uuid
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from pitch_coach_backend.module.chat.repository import ChatRepository
from pitch_coach_backend.module.chat.service.goal_check import deactivate_goals_without_base
from pitch_coach_backend.module.chat.service.owned import parse_uuid
from pitch_coach_backend.module.chat.service.stale import MASKED_TEXT, run_source_ids

CLEANUP_LEASE = timedelta(hours=1)
ALERT_AFTER = timedelta(hours=2)


def run_cleanup(db: Session, owner: str, now: datetime) -> int | None:
    repo = ChatRepository(db)
    lease = repo.take_cleanup(owner, now + CLEANUP_LEASE, now)
    if lease is None:
        db.rollback()
        return None
    token = lease.fencing_token
    db.commit()
    cleaned = clean_deleted_sources(db)
    lease = repo.get_cleanup_lease()
    if lease is not None and lease.fencing_token == token:
        lease.lease_until = now
        db.commit()
    return cleaned


def cleanup_delayed(db: Session, now: datetime) -> bool:
    lease = ChatRepository(db).get_cleanup_lease()
    return lease is not None and lease.updated_at < now - ALERT_AFTER


def clean_deleted_sources(db: Session) -> int:
    deactivate_goals_without_base(db)
    repo = ChatRepository(db)
    cleaned = _mask_answers(repo) + _mask_runs(repo) + _clear_goals(repo) + _mask_summaries(repo)
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


def _mask_summaries(repo: ChatRepository) -> int:
    summaries = repo.lock_unmasked_summaries()
    deleted = _deleted_ids(
        repo,
        {pitch_id for summary in summaries for pitch_id in summary.source_pitch_ids or []},
        {take_id for summary in summaries for take_id in summary.source_take_ids or []},
    )
    masked = 0
    for summary in summaries:
        if deleted & {*(summary.source_pitch_ids or []), *(summary.source_take_ids or [])}:
            # 항목마다 근거를 나눌 수 없어 통째로 비운다. 덮은 메시지는 그대로 두어
            # 가린 답을 다시 요약하지 않고, 판을 올려 지우기 전 요약을 받은 턴이
            # 새 요약을 저장하지 못하게 한다
            summary.items = []
            summary.source_pitch_ids = []
            summary.source_take_ids = []
            summary.summary_version += 1
            masked += 1
    return masked


def _mask_runs(repo: ChatRepository) -> int:
    masked = 0
    for run in repo.list_finished_runs_with_content():
        actions = repo.list_actions(run.id)
        pitch_ids, take_ids = run_source_ids(
            run.read_sources, (action.source_take_ids for action in actions)
        )
        if not _deleted_ids(repo, pitch_ids, take_ids):
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


def _deleted_ids(repo: ChatRepository, pitch_ids: set[Any], take_ids: set[Any]) -> set[Any]:
    """없는 근거 ID 를 저장된 값 그대로 돌려준다. 부르는 쪽이 저장된 값과 바로 대조한다.

    AI 가 대문자나 하이픈 없는 UUID 를 냈어도 저장된 문자열로 맞으므로 빠지지 않는다. 읽지 못하는
    ID 도 없는 것으로 본다.
    """
    pitches = _parse(pitch_ids)
    takes = _parse(take_ids)
    existing_pitches = repo.existing_pitch_ids(pitches) if pitches else set()
    existing_takes = repo.existing_take_ids(takes) if takes else set()
    return {value for value in pitch_ids if parse_uuid(value) not in existing_pitches} | {
        value for value in take_ids if parse_uuid(value) not in existing_takes
    }


def _parse(values: set[Any]) -> set[uuid.UUID]:
    return {parsed for value in values if (parsed := parse_uuid(value)) is not None}
