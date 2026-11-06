from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel
from sqlalchemy.orm import Session

from pitch_coach_backend.module.chat.ai_response import (
    ACTION_ARGS,
    ACTION_AUTONOMY,
    SaveGoalArgs,
    args_hash,
)
from pitch_coach_backend.module.chat.dto import ActionResultDTO
from pitch_coach_backend.module.chat.entity import ChatRun
from pitch_coach_backend.module.chat.repository import ChatRepository
from pitch_coach_backend.module.chat.service.fetch import fetch_pitch, fetch_take
from pitch_coach_backend.module.chat.service.goal import save_goal
from pitch_coach_backend.module.chat.service.memory import memory_cards, new_memory_cards
from pitch_coach_backend.module.chat.service.result import UNCONFIRMED, close_action, failed
from pitch_coach_backend.module.chat.service.run import (
    ClaimedRun,
    RunLost,
    lock_conversation_of,
    lock_current,
    lock_or_cancel,
)

type ActionHandler = Callable[[Session, ChatRun, dict[str, Any], BaseModel], ActionResultDTO]

ACTION_HANDLERS: dict[str, ActionHandler] = {
    "FETCH_TAKE": fetch_take,
    "FETCH_PITCH": fetch_pitch,
    "SAVE_GOAL": save_goal,
}


def begin_action(db: Session, run: ClaimedRun, action: dict[str, Any]) -> ActionResultDTO | None:
    row = lock_current(db, run)
    if row is None:
        raise RunLost(run.run_id)
    saved = ChatRepository(db).get_action(row.id, action["action_id"])
    if saved.status in ("SUCCEEDED", "FAILED"):
        return ActionResultDTO.model_validate(
            {key: value for key, value in saved.result.items() if key != "review_revision"}
        )
    if _invalid(action):
        return close_action(db, row, action, failed(action, "INVALID_ACTION", UNCONFIRMED))
    if (
        action["autonomy"] == "ASK"
        and args_hash(action["type"], action["args"]) != action["args_hash"]
    ):
        changed = failed(action, "APPROVAL_ARGS_CHANGED", UNCONFIRMED).model_copy(
            update={"executed_args_hash": action["args_hash"]}
        )
        return close_action(db, row, action, changed)
    saved.status = "RUNNING"
    db.commit()
    return None


def hold_for_approval(
    db: Session, run: ClaimedRun, action: dict[str, Any], profile_updates: Any
) -> ActionResultDTO | None:
    conversation = lock_conversation_of(db, run)
    row = lock_current(db, run)
    if row is None:
        raise RunLost(run.run_id)
    if _invalid(action):
        return close_action(db, row, action, failed(action, "INVALID_ACTION", UNCONFIRMED))
    repo = ChatRepository(db)
    saved = memory_cards(row)
    new = new_memory_cards(repo, row, conversation, profile_updates, saved)
    row.interim_message = {**(row.interim_message or {}), "memory_saved": [*saved, *new]}
    row.status = "WAITING_APPROVAL"
    row.approval_started_at = datetime.now(UTC)
    row.row_revision += 1
    db.commit()
    return None


def run_action(db: Session, run: ClaimedRun, action: dict[str, Any]) -> ActionResultDTO:
    args = ACTION_ARGS[action["type"]].model_validate(action["args"])
    if isinstance(args, SaveGoalArgs):
        goal = args.goal
        lock_or_cancel(db, run, {goal.get("pitch_id")}, {(goal.get("base") or {}).get("take_id")})
        lock_conversation_of(db, run)
    row = lock_current(db, run)
    if row is None:
        raise RunLost(run.run_id)
    result = ACTION_HANDLERS[action["type"]](db, row, action, args)
    saved = ChatRepository(db).get_action(row.id, action["action_id"])
    saved.status = "SUCCEEDED" if result.status != "FAILED" else "FAILED"
    saved.result = result.model_dump(mode="json")
    db.commit()
    return result


def _invalid(action: dict[str, Any]) -> bool:
    kind = action["type"]
    if kind not in (*ACTION_HANDLERS, "REASSESS") or action["autonomy"] != ACTION_AUTONOMY[kind]:
        return True
    try:
        ACTION_ARGS[kind].model_validate(action["args"])
    except ValueError:
        return True
    return False
