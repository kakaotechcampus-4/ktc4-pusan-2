import uuid
from typing import Any

from sqlalchemy.orm import Session

from pitch_coach_backend.module.chat.ai_response import StartPracticeArgs
from pitch_coach_backend.module.chat.entity import ChatAction, ChatRun
from pitch_coach_backend.module.chat.repository import ChatRepository
from pitch_coach_backend.module.chat.service.authorization import authorization
from pitch_coach_backend.module.chat.service.run import digest
from pitch_coach_backend.module.pitch.repository import PitchRepository


def start_practice(db: Session, repo: ChatRepository, row: ChatRun, data: dict[str, Any]) -> None:
    actions = [action for action in data["actions"] if action["type"] == "START_PRACTICE"]
    if not actions:
        return
    action = actions[0]
    decision = _practice_decision(db, row, action)
    cards = [card for card in data["message"]["cards"] if card.get("type") != "START_PRACTICE"]
    if decision is not None:
        cards.append({"type": "START_PRACTICE", **decision})
        repo.add(
            ChatAction(
                conversation_id=row.conversation_id,
                user_id=row.user_id,
                run_id=row.id,
                action_id=action["action_id"],
                type="START_PRACTICE",
                args=action["args"],
                args_digest=digest(action["args"]),
                autonomy=action["autonomy"],
                idempotency_key=action["idempotency_key"],
                args_hash=action["args_hash"],
                status="SUCCEEDED",
                result=decision,
            )
        )
    data["message"]["cards"] = cards


def _practice_decision(db: Session, row: ChatRun, action: dict[str, Any]) -> dict[str, Any] | None:
    if row.first_trigger == "TAKE_REVIEWED" or action["autonomy"] != "BUTTON":
        return None
    try:
        args = StartPracticeArgs.model_validate(action["args"])
        pitch_id = uuid.UUID(args.pitch_id)
    except ValueError:
        return None
    if PitchRepository(db).get_owned(pitch_id, row.user_id) is None:
        return None
    verdict = authorization(
        row.input.get("user_message"), action.get("authorized_by"), "START_PRACTICE"
    )
    if verdict == "REJECTED":
        return None
    return {"pitch_id": str(pitch_id)}
