from typing import Any

from sqlalchemy.orm import Session

from pitch_coach_backend.module.chat.ai_response import StartPracticeArgs
from pitch_coach_backend.module.chat.dto import AgentTrigger
from pitch_coach_backend.module.chat.entity import ChatRun
from pitch_coach_backend.module.chat.repository import ChatRepository
from pitch_coach_backend.module.chat.service.authorization import authorization
from pitch_coach_backend.module.chat.service.owned import owned_pitch
from pitch_coach_backend.module.chat.service.result import action_row


def start_practice(
    db: Session,
    repo: ChatRepository,
    row: ChatRun,
    actions: list[dict[str, Any]],
    cards: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """연습 시작 행동이 있으면 BE 가 확인한 버튼 카드로 바꾼 답 카드를 돌려준다.

    AI 가 직접 넣은 연습 시작 카드는 버린다. 쓸 수 있는 피치가 아니면 버튼을 내지 않는다.
    """
    action = next((action for action in actions if action["type"] == "START_PRACTICE"), None)
    if action is None:
        return cards
    decision = _practice_decision(db, row, action)
    kept = [card for card in cards if card.get("type") != "START_PRACTICE"]
    if decision is None:
        return kept
    repo.add(action_row(row, action, status="SUCCEEDED", result=decision))
    return [*kept, {"type": "START_PRACTICE", **decision}]


def _practice_decision(db: Session, row: ChatRun, action: dict[str, Any]) -> dict[str, Any] | None:
    if row.first_trigger == AgentTrigger.TAKE_REVIEWED or action["autonomy"] != "BUTTON":
        return None
    try:
        args = StartPracticeArgs.model_validate(action["args"])
    except ValueError:
        return None
    pitch = owned_pitch(db, row.user_id, args.pitch_id)
    if pitch is None:
        return None
    verdict = authorization(
        row.input.get("user_message"), action.get("authorized_by"), "START_PRACTICE"
    )
    if verdict == "REJECTED":
        return None
    return {"pitch_id": str(pitch.id)}
