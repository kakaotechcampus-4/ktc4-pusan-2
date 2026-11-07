import uuid
from collections.abc import Sequence
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from pitch_coach_backend.module.chat.repository import ChatRepository
from pitch_coach_backend.module.chat.service.goal_check import deactivate_goals_without_base
from pitch_coach_backend.module.chat.service.stale import MASKED_TEXT

CLEANUP_LEASE = timedelta(hours=1)
ALERT_AFTER = timedelta(hours=2)
# 지운 id 를 정리한 뒤에도 남겨 두는 기간. 삭제 때 진행 중이던 실행은 끝나고 나서야 고를 수 있고,
# 턴이 쥔 요약은 다음 주기로 넘어간다. 가장 긴 승인 대기(30분)보다 넉넉하면 된다
DELETED_SOURCE_KEEP = timedelta(days=7)


def run_cleanup(db: Session, owner: str, now: datetime) -> int | None:
    repo = ChatRepository(db)
    lease = repo.take_cleanup(owner, now + CLEANUP_LEASE, now)
    if lease is None:
        db.rollback()
        return None
    token = lease.fencing_token
    db.commit()
    cleaned = clean_deleted_sources(db, now)
    lease = repo.get_cleanup_lease()
    if lease is not None and lease.fencing_token == token:
        lease.lease_until = now
        db.commit()
    return cleaned


def cleanup_delayed(db: Session, now: datetime) -> bool:
    lease = ChatRepository(db).get_cleanup_lease()
    return lease is not None and lease.updated_at < now - ALERT_AFTER


def clean_deleted_sources(db: Session, now: datetime) -> int:
    deactivate_goals_without_base(db)
    repo = ChatRepository(db)
    sources = repo.list_deleted_sources()
    deleted = [str(source.source_id) for source in sources]
    cleaned = (
        _mask_answers(repo, deleted)
        + _mask_runs(repo, deleted)
        + _clear_goals(repo, [source.source_id for source in sources])
        + _mask_summaries(repo, deleted)
    )
    # 이번에 읽은 것만 지운다. 작업이 오래 멈춰도 한 번도 보지 못한 id 를 잃지 않는다
    cutoff = now - DELETED_SOURCE_KEEP
    repo.delete_deleted_sources(
        [source.source_id for source in sources if source.deleted_at < cutoff]
    )
    db.commit()
    return cleaned


def _mask_answers(repo: ChatRepository, deleted: Sequence[str]) -> int:
    messages = repo.list_unmasked_answers_using(deleted, MASKED_TEXT)
    for message in messages:
        message.text = MASKED_TEXT
        message.claims = [{"kind": "SYSTEM", "text": MASKED_TEXT}]
        message.cards = None
        message.plan = None
        message.trace = None
        message.evidence = None
        message.basis = None
        message.assessments = None
        message.sources = None
    return len(messages)


def _mask_summaries(repo: ChatRepository, deleted: Sequence[str]) -> int:
    summaries = repo.lock_unmasked_summaries_using(deleted)
    for summary in summaries:
        # 항목마다 근거를 나눌 수 없어 통째로 비운다. 덮은 메시지는 그대로 두어
        # 가린 답을 다시 요약하지 않고, 판을 올려 지우기 전 요약을 받은 턴이
        # 새 요약을 저장하지 못하게 한다
        summary.items = []
        summary.source_pitch_ids = []
        summary.source_take_ids = []
        summary.summary_version += 1
    return len(summaries)


def _mask_runs(repo: ChatRepository, deleted: Sequence[str]) -> int:
    runs = repo.list_finished_runs_using(deleted)
    for run in runs:
        run.state = None
        run.interim_message = None
        run.plan = None
        run.trace = None
        repo.delete_run_events(run.id)
        for action in repo.list_actions(run.id):
            action.result = None
    return len(runs)


def _clear_goals(repo: ChatRepository, deleted: Sequence[uuid.UUID]) -> int:
    goals = repo.list_goals_based_on(deleted)
    for goal in goals:
        goal.goal = None
        goal.status = None
    return len(goals)
