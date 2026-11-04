from typing import Any

from sqlalchemy.orm import Session

from pitch_coach_backend.module.chat.dto import ActionResultDTO, AgentErrorDTO
from pitch_coach_backend.module.chat.entity import ChatRun
from pitch_coach_backend.module.chat.repository import ChatRepository


def failed(action: dict[str, Any], code: str, message: str) -> ActionResultDTO:
    return ActionResultDTO(
        action_id=action["action_id"],
        type=action["type"],
        status="FAILED",
        data=None,
        error=AgentErrorDTO(code=code, message=message),
    )


def succeeded(action: dict[str, Any], data: dict[str, Any]) -> ActionResultDTO:
    return ActionResultDTO(
        action_id=action["action_id"],
        type=action["type"],
        status="SUCCEEDED",
        data=data,
        error=None,
    )


def ambiguous(action: dict[str, Any], data: dict[str, Any]) -> ActionResultDTO:
    return ActionResultDTO(
        action_id=action["action_id"],
        type=action["type"],
        status="AMBIGUOUS",
        data=data,
        error=None,
    )


def close_action(
    db: Session, row: ChatRun, action: dict[str, Any], result: ActionResultDTO
) -> ActionResultDTO:
    saved = ChatRepository(db).get_action(row.id, action["action_id"])
    saved.status = "FAILED"
    saved.result = result.model_dump(mode="json")
    db.commit()
    return result
