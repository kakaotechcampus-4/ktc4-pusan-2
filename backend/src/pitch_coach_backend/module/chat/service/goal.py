import uuid
from typing import Any

from sqlalchemy.orm import Session

from pitch_coach_backend.module.chat.ai_response import SaveGoalArgs
from pitch_coach_backend.module.chat.dto import ActionResultDTO
from pitch_coach_backend.module.chat.entity import ChatGoal, ChatRun
from pitch_coach_backend.module.chat.repository import ChatRepository
from pitch_coach_backend.module.chat.service.authorization import authorization
from pitch_coach_backend.module.chat.service.fetch import owned_take
from pitch_coach_backend.module.chat.service.request import (
    ProposalNotFound,
    completed_fully,
    goal_view,
    selected_proposal,
)
from pitch_coach_backend.module.chat.service.result import UNCONFIRMED, failed, succeeded
from pitch_coach_backend.module.chat.service.run import digest
from pitch_coach_backend.module.take.repository import TakeRepository

GOAL_METRICS = {
    "audience_ratio": ("GAZE", "gt_base"),
    "filler_per_min": ("FILLER", "lt_base"),
    "cpm": ("SPEED", "in_range"),
    "duration_ms": ("TIME", "in_range"),
}
METRIC_NOT_ALLOWED = "GOAL_METRIC_NOT_ALLOWED"
OPERATOR_MISMATCH = "GOAL_OPERATOR_MISMATCH"
BASE_MISMATCH = "GOAL_BASE_MISMATCH"


class GoalRejected(Exception):
    pass


def save_goal(
    db: Session, row: ChatRun, action: dict[str, Any], args: SaveGoalArgs
) -> ActionResultDTO:
    repo = ChatRepository(db)
    if row.first_trigger == "TAKE_REVIEWED":
        return failed(action, "NOT_ALLOWED", UNCONFIRMED)
    preapproved = action.get("preapproved")
    if preapproved is not None:
        if not _preapproval_matches(repo, row, preapproved, args.goal):
            return failed(action, "PREAPPROVAL_MISMATCH", UNCONFIRMED)
    elif not _goal_authorized(repo, row, action):
        return failed(action, "APPROVAL_REQUIRED", UNCONFIRMED)
    if repo.count_succeeded_actions(row.id, "SAVE_GOAL") > 0:
        return failed(action, "NOT_ALLOWED", UNCONFIRMED)
    try:
        goal = _rebuild_goal(db, repo, row.user_id, args.goal)
    except GoalRejected as rejected:
        return failed(action, rejected.args[0], UNCONFIRMED)
    conversation = repo.lock_conversation(row.conversation_id)
    if conversation.goal_version != args.expected_goal_version:
        return failed(action, "GOAL_VERSION_CONFLICT", "이미 다른 목표로 바뀌었어요.")
    saved = repo.get_action(row.id, action["action_id"])
    version = conversation.goal_version + 1
    repo.add(
        ChatGoal(
            conversation_id=conversation.id,
            version=version,
            goal=goal,
            status="OPEN",
            base_take_id=uuid.UUID(goal["base"]["take_id"]),
            first_take_number=TakeRepository(db).next_take_number(uuid.UUID(goal["pitch_id"])),
            action_id=saved.id,
        )
    )
    conversation.goal_version = version
    saved.source_take_ids = [goal["base"]["take_id"]]
    db.flush()
    return succeeded(action, {"goal": goal_view(repo.get_goal(conversation.id, version))})


def _goal_authorized(repo: ChatRepository, row: ChatRun, action: dict[str, Any]) -> bool:
    if repo.get_action(row.id, action["action_id"]).approved:
        return True
    original = row.input.get("user_message")
    return authorization(original, action.get("authorized_by"), "SAVE_GOAL") == "OK"


def _preapproval_matches(
    repo: ChatRepository, row: ChatRun, preapproved: dict[str, Any], goal: dict[str, Any]
) -> bool:
    choice = row.input.get("choice")
    if not choice or preapproved.get("proposal_id") != choice["proposal_id"]:
        return False
    try:
        proposal = selected_proposal(repo, row.conversation_id, choice)
    except ProposalNotFound:
        return False
    return proposal["content_hash"] == preapproved.get("content_hash") == digest(goal)[:16]


def _rebuild_goal(
    db: Session, repo: ChatRepository, user_id: uuid.UUID, goal: dict[str, Any]
) -> dict[str, Any]:
    metric = goal.get("metric")
    if metric not in GOAL_METRICS:
        raise GoalRejected(METRIC_NOT_ALLOWED)
    area, operator = GOAL_METRICS[metric]
    slide_number = goal.get("slide_number")
    if goal.get("operator") != operator or (metric == "duration_ms" and slide_number is not None):
        raise GoalRejected(OPERATOR_MISMATCH)
    base = goal.get("base") or {}
    take = owned_take(db, repo, user_id, str(goal.get("pitch_id")), str(base.get("take_id")))
    if take is None:
        raise GoalRejected(BASE_MISMATCH)
    slides = repo.slide_numbers([take.Take.id]).get(take.Take.id, set())
    if completed_fully(take.Take, take.page_count, slides) is not True:
        raise GoalRejected(BASE_MISMATCH)
    result = next(iter(repo.list_ai_results(take.Take.id, ["TAKE_RESULT"])), None)
    if result is None or not isinstance(result.payload, dict):
        raise GoalRejected(BASE_MISMATCH)
    rule_version = ((result.payload.get("criteria") or {}).get(area) or {}).get("criteria_version")
    if not isinstance(rule_version, str):
        raise GoalRejected(BASE_MISMATCH)
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
        raise GoalRejected(BASE_MISMATCH)
    return rebuilt


def _metric_value(
    payload: dict[str, Any], area: str, metric: str, slide_number: int | None
) -> float:
    section = (payload.get("areas") or {}).get(area) or {}
    if section.get("unmeasured_reason"):
        raise GoalRejected(BASE_MISMATCH)
    if slide_number is None:
        value = (section.get("take") or {}).get(metric)
    else:
        item = next(
            (s for s in section.get("slides") or [] if s.get("slide_number") == slide_number),
            None,
        )
        if item is None:
            raise GoalRejected(BASE_MISMATCH)
        value = (item.get("metrics") or {}).get(metric)
    if not _finite(value) or value < 0 or (metric == "audience_ratio" and value > 1):
        raise GoalRejected(BASE_MISMATCH)
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
        raise GoalRejected(BASE_MISMATCH)
    if any(bound is not None and not _finite(bound) for bound in (low, high)):
        raise GoalRejected(BASE_MISMATCH)
    if low is not None and high is not None and low > high:
        raise GoalRejected(BASE_MISMATCH)
    return {"low": low, "high": high}


def _check_rule(goal: dict[str, Any]) -> None:
    rule_version = goal["goal_rule_version"]
    if not isinstance(rule_version, str) or not rule_version:
        raise GoalRejected(OPERATOR_MISMATCH)
    min_delta = goal["min_delta"]
    if goal["operator"] == "in_range":
        if min_delta is not None:
            raise GoalRejected(OPERATOR_MISMATCH)
    elif not _finite(min_delta) or min_delta <= 0:
        raise GoalRejected(OPERATOR_MISMATCH)


def _finite(value: Any) -> bool:
    return (
        isinstance(value, int | float)
        and not isinstance(value, bool)
        and value == value
        and value not in (float("inf"), float("-inf"))
    )
