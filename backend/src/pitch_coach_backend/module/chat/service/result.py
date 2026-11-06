from typing import Any

from sqlalchemy.orm import Session

from pitch_coach_backend.module.chat.ai_response import args_hash
from pitch_coach_backend.module.chat.dto import ActionResultDTO, AgentErrorDTO
from pitch_coach_backend.module.chat.entity import ChatAction, ChatRun
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


def save_result(
    saved: ChatAction, result: ActionResultDTO, *, review_revision: int | None = None
) -> None:
    """행동 결과를 그 행동 행에 적는다. 커밋은 부르는 쪽이 한다.

    후보가 여럿이라 되묻는 결과(AMBIGUOUS)도 행동은 끝까지 실행한 것이라 성공으로 둔다.
    다시 평가는 결과를 만든 공식 리뷰 판을 함께 남긴다.
    """
    stored = result.model_dump(mode="json")
    if review_revision is not None:
        stored["review_revision"] = review_revision
    saved.status = "FAILED" if result.status == "FAILED" else "SUCCEEDED"
    saved.result = stored


def close_action(
    db: Session, row: ChatRun, action: dict[str, Any], result: ActionResultDTO
) -> ActionResultDTO:
    """실행하지 않고 끝낸 행동의 결과를 저장하고 커밋한다."""
    save_result(ChatRepository(db).get_action(row.id, action["action_id"]), result)
    db.commit()
    return result
