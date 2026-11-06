import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import Row
from sqlalchemy.orm import Session

from pitch_coach_backend.module.chat.entity import ChatConversation, ChatGoal, ChatLease, ChatRun
from pitch_coach_backend.module.chat.repository import ChatRepository
from pitch_coach_backend.module.chat.service.run import seoul_day

GOAL_CHECKS_PER_CYCLE = 100
GOAL_CHECK_DAILY_LIMIT = 30
SERVICE_DAILY_TURNS = 1000
GOAL_LEASE = timedelta(minutes=5)
TURN_DEADLINE = timedelta(seconds=120)
REVIEW_GIVEN_UP = ("FAILED", "SKIPPED")


@dataclass(frozen=True)
class GoalCheckTarget:
    conversation_id: uuid.UUID
    user_id: uuid.UUID
    goal_version: int
    pitch_id: uuid.UUID
    take_id: uuid.UUID
    review_revision: int


def pick_targets(db: Session, now: datetime) -> list[GoalCheckTarget]:
    """열린 목표마다 다음에 확인할 리뷰를 고른다. 목표 수와 관계없이 쿼리 세 번으로 읽는다."""
    repo = ChatRepository(db)
    goals = repo.list_open_goals()
    pitch_ids = {goal.conversation_id: uuid.UUID(goal.pitch_id) for goal in goals}
    first_numbers: dict[uuid.UUID, int] = {}
    for goal in goals:
        pitch_id = pitch_ids[goal.conversation_id]
        first_numbers[pitch_id] = min(
            goal.first_take_number, first_numbers.get(pitch_id, goal.first_take_number)
        )
    takes: dict[uuid.UUID, list[Row]] = defaultdict(list)
    for take in repo.list_takes_from(first_numbers):
        takes[take.pitch_id].append(take)
    leases = {
        (lease.conversation_id, lease.goal_version, lease.take_id, lease.review_revision): lease
        for lease in repo.list_goal_leases([(goal.conversation_id, goal.version) for goal in goals])
    }
    targets: list[GoalCheckTarget] = []
    for goal in goals:
        pitch_id = pitch_ids[goal.conversation_id]
        target = _next_review(goal, pitch_id, takes[pitch_id], leases, now)
        if target is not None:
            targets.append(target)
        if len(targets) == GOAL_CHECKS_PER_CYCLE:
            break
    return targets


def _next_review(
    goal: Row,
    pitch_id: uuid.UUID,
    takes: list[Row],
    leases: dict[tuple[uuid.UUID, int, uuid.UUID, int], ChatLease],
    now: datetime,
) -> GoalCheckTarget | None:
    for take in takes:
        if take.take_number < goal.first_take_number:
            continue
        if take.status == "FAILED" or take.review_status in REVIEW_GIVEN_UP:
            continue
        if take.review_status != "DONE" or take.review_revision is None:
            return None
        lease = leases.get((goal.conversation_id, goal.version, take.id, take.review_revision))
        if lease is not None and lease.status == "DONE":
            continue
        if lease is not None and lease.lease_until > now:
            return None
        return GoalCheckTarget(
            conversation_id=goal.conversation_id,
            user_id=goal.user_id,
            goal_version=goal.version,
            pitch_id=pitch_id,
            take_id=take.id,
            review_revision=take.review_revision,
        )
    return None


def claim_target(
    db: Session, target: GoalCheckTarget, owner: str, now: datetime
) -> uuid.UUID | None:
    repo = ChatRepository(db)
    conversation = repo.lock_conversation(target.conversation_id)
    goal = repo.get_goal(target.conversation_id, target.goal_version)
    if (
        conversation is None
        or conversation.goal_version != target.goal_version
        or goal is None
        or goal.status != "OPEN"
        or repo.has_running_run(conversation.id)
    ):
        db.rollback()
        return None
    lease = repo.take_goal_check(
        conversation_id=target.conversation_id,
        goal_version=target.goal_version,
        take_id=target.take_id,
        review_revision=target.review_revision,
        owner=owner,
        until=now + GOAL_LEASE,
        now=now,
    )
    day = seoul_day(now)
    if (
        lease is None
        or not repo.charge_goal_check(target.user_id, day, GOAL_CHECK_DAILY_LIMIT)
        or not repo.reserve_service_turn(day, SERVICE_DAILY_TURNS)
    ):
        db.rollback()
        return None
    run = ChatRun(
        conversation_id=target.conversation_id,
        user_id=target.user_id,
        first_trigger="TAKE_REVIEWED",
        status="QUEUED",
        scope_pitch_id=target.pitch_id,
        input={
            "review_event": {
                "take_id": str(target.take_id),
                "review_revision": target.review_revision,
                "goal_version": target.goal_version,
            },
            "page": {
                "route": "report",
                "pitch_id": str(target.pitch_id),
                "take_id": str(target.take_id),
                "screen": None,
            },
        },
        accepted_at=now,
        deadline_at=now + TURN_DEADLINE,
        fencing_token=lease.fencing_token,
    )
    repo.add(run)
    db.flush()
    lease.run_id = run.id
    db.commit()
    return run.id


def deactivate_goals_without_base(db: Session) -> int:
    repo = ChatRepository(db)
    deactivated = 0
    for goal in repo.list_goals_without_base():
        conversation = repo.lock_conversation(goal.conversation_id)
        if conversation is None or conversation.goal_version != goal.version:
            db.rollback()
            continue
        version = goal.version + 1
        repo.add(ChatGoal(conversation_id=conversation.id, version=version))
        conversation.goal_version = version
        db.commit()
        deactivated += 1
    return deactivated


def settle_goal_check(
    repo: ChatRepository, row: ChatRun, conversation: ChatConversation, result: dict[str, Any]
) -> bool:
    lease = repo.lock_goal_check(row.id)
    if lease is None or lease.status != "PENDING" or lease.fencing_token != row.fencing_token:
        return False
    lease.status = "DONE"
    event = row.input["review_event"]
    goal = repo.get_goal(conversation.id, event["goal_version"])
    if (
        conversation.goal_version != event["goal_version"]
        or goal is None
        or goal.status != "OPEN"
        or repo.get_review_revision(uuid.UUID(event["take_id"])) != event["review_revision"]
        or (result["goal_version"], result["take_id"], result["review_revision"])
        != (event["goal_version"], event["take_id"], event["review_revision"])
    ):
        return False
    if result["terminal"]:
        goal.status = result["status"]
    return True
