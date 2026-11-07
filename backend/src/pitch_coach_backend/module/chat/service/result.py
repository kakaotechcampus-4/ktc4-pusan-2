from typing import Any

from sqlalchemy.orm import Session

from pitch_coach_backend.module.chat.ai_response import args_hash
from pitch_coach_backend.module.chat.dto import ActionResultDTO, AgentErrorDTO
from pitch_coach_backend.module.chat.entity import ChatRun
from pitch_coach_backend.module.chat.repository import ChatRepository

UNCONFIRMED = "작업 완료를 확인하지 못했어요."


def executed_args_hash(action: dict[str, Any]) -> str | None:
    if action.get("autonomy") != "ASK":
        return None
    return args_hash(action["type"], action["args"])


def failed(action: dict[str, Any], code: str, message: str) -> ActionResultDTO:
    return ActionResultDTO(
        action_id=action["action_id"],
        type=action["type"],
        status="FAILED",
        data=None,
        error=AgentErrorDTO(code=code, message=message),
        executed_args_hash=executed_args_hash(action),
    )


def succeeded(action: dict[str, Any], data: dict[str, Any]) -> ActionResultDTO:
    return ActionResultDTO(
        action_id=action["action_id"],
        type=action["type"],
        status="SUCCEEDED",
        data=data,
        error=None,
        executed_args_hash=executed_args_hash(action),
    )


def ambiguous(action: dict[str, Any], data: dict[str, Any]) -> ActionResultDTO:
    return ActionResultDTO(
        action_id=action["action_id"],
        type=action["type"],
        status="AMBIGUOUS",
        data=data,
        error=None,
        executed_args_hash=executed_args_hash(action),
    )


def close_action(
    db: Session, row: ChatRun, action: dict[str, Any], result: ActionResultDTO
) -> ActionResultDTO:
    saved = ChatRepository(db).get_action(row.id, action["action_id"])
    saved.status = "FAILED"
    saved.result = result.model_dump(mode="json")
    db.commit()
    return result
