from collections.abc import Callable
from typing import Any

from pydantic import BaseModel
from sqlalchemy.orm import Session

from pitch_coach_backend.module.chat.ai_response import ACTION_ARGS, ACTION_AUTONOMY, SaveGoalArgs
from pitch_coach_backend.module.chat.dto import ActionResultDTO
from pitch_coach_backend.module.chat.entity import ChatRun
from pitch_coach_backend.module.chat.repository import ChatRepository
from pitch_coach_backend.module.chat.service.fetch import fetch_pitch, fetch_take
from pitch_coach_backend.module.chat.service.goal import save_goal
from pitch_coach_backend.module.chat.service.result import close_action, failed
from pitch_coach_backend.module.chat.service.run import ClaimedRun, RunLost, lock_current, lock_ids

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
        return close_action(
            db, row, action, failed(action, "VALIDATION_ERROR", "실행할 수 없는 행동이에요.")
        )
    saved.status = "RUNNING"
    db.commit()
    return None


def run_action(db: Session, run: ClaimedRun, action: dict[str, Any]) -> ActionResultDTO:
    args = ACTION_ARGS[action["type"]].model_validate(action["args"])
    if isinstance(args, SaveGoalArgs):
        goal = args.goal
        lock_ids(db, {goal.get("pitch_id")}, {(goal.get("base") or {}).get("take_id")})
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
