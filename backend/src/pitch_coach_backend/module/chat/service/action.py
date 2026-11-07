from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from pitch_coach_backend.module.chat.ai_response import (
    ACTION_ARGS,
    ACTION_AUTONOMY,
    ActionArgs,
    SaveGoalArgs,
    args_hash,
)
from pitch_coach_backend.module.chat.dto import ActionResultDTO
from pitch_coach_backend.module.chat.entity import ChatAction, ChatRun
from pitch_coach_backend.module.chat.repository import ChatRepository
from pitch_coach_backend.module.chat.service.fetch import fetch_pitch, fetch_take
from pitch_coach_backend.module.chat.service.goal import save_goal
from pitch_coach_backend.module.chat.service.memory import memory_cards, new_memory_cards
from pitch_coach_backend.module.chat.service.result import (
    UNCONFIRMED,
    close_action,
    failed,
    save_result,
)
from pitch_coach_backend.module.chat.service.run import (
    ClaimedRun,
    RunLost,
    lock_conversation_of,
    lock_current,
    lock_or_cancel,
)

type ActionHandler = Callable[
    [Session, ChatRun, ChatAction, dict[str, Any], ActionArgs], ActionResultDTO
]

ACTION_HANDLERS: dict[str, ActionHandler] = {
    "FETCH_TAKE": fetch_take,
    "FETCH_PITCH": fetch_pitch,
    "SAVE_GOAL": save_goal,
}


def begin_action(
    db: Session, run: ClaimedRun, action: dict[str, Any]
) -> ActionResultDTO | ActionArgs:
    """행동을 실행 중으로 두고 검사한 인자를 돌려준다. 이미 끝났거나 실행하면 안 되면 그 결과다."""
    row = lock_current(db, run)
    if row is None:
        raise RunLost(run.run_id)
    saved = ChatRepository(db).get_action(row.id, action["action_id"])
    if saved.status in ("SUCCEEDED", "FAILED"):
        return ActionResultDTO.model_validate(
            {key: value for key, value in saved.result.items() if key != "review_revision"}
        )
    args = _parsed_args(action)
    if args is None:
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
    return args


def hold_for_approval(
    db: Session, run: ClaimedRun, action: dict[str, Any], profile_updates: Any
) -> ActionResultDTO | None:
    conversation = lock_conversation_of(db, run)
    row = lock_current(db, run)
    if row is None:
        raise RunLost(run.run_id)
    if _parsed_args(action) is None:
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


def run_action(
    db: Session, run: ClaimedRun, action: dict[str, Any], args: ActionArgs
) -> ActionResultDTO:
    """`begin_action` 이 검사한 인자로 행동을 실행하고 결과를 저장한다."""
    if isinstance(args, SaveGoalArgs):
        goal = args.goal
        lock_or_cancel(db, run, {goal.get("pitch_id")}, {(goal.get("base") or {}).get("take_id")})
        lock_conversation_of(db, run)
    row = lock_current(db, run)
    if row is None:
        raise RunLost(run.run_id)
    saved = ChatRepository(db).get_action(row.id, action["action_id"])
    result = ACTION_HANDLERS[action["type"]](db, row, saved, action, args)
    save_result(saved, result)
    db.commit()
    return result


def _parsed_args(action: dict[str, Any]) -> ActionArgs | None:
    """실행할 수 있는 행동이면 검사한 인자. 다시 평가는 턴 실행기가 따로 실행한다."""
    kind = action["type"]
    if kind not in (*ACTION_HANDLERS, "REASSESS") or action["autonomy"] != ACTION_AUTONOMY[kind]:
        return None
    try:
        return ACTION_ARGS[kind].model_validate(action["args"])
    except ValueError:
        return None
