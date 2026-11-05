import asyncio
import copy
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import Row
from sqlalchemy.orm import Session

from pitch_coach_backend.module.chat.ai_client import request_review_once
from pitch_coach_backend.module.chat.ai_response import ReassessArgs
from pitch_coach_backend.module.chat.dto import ActionResultDTO
from pitch_coach_backend.module.chat.repository import ChatRepository
from pitch_coach_backend.module.chat.service.result import close_action, failed, succeeded
from pitch_coach_backend.module.chat.service.run import (
    SEOUL,
    ClaimedRun,
    RunLost,
    digest,
    lock_current,
    lock_or_cancel,
)
from pitch_coach_backend.module.pitch.repository import PitchRepository
from pitch_coach_backend.module.take.dto import UserCriteriaDTO
from pitch_coach_backend.module.take.exception import NonExistentTake
from pitch_coach_backend.module.take.service.review import build_review_request

REASSESS_DAILY_LIMIT = 30
EMPTY_CRITERIA = {"display_criteria": [], "targets": [], "script_used": None}
NO_REVIEW = "이 연습은 다시 평가할 자료가 부족해요."


@dataclass(frozen=True)
class PreparedReassess:
    take_id: uuid.UUID
    review_revision: int
    official: dict[str, Any]
    before: dict[str, Any]
    after: dict[str, Any]
    before_digest: str


def prepare_reassess(
    db: Session, run: ClaimedRun, action: dict[str, Any]
) -> PreparedReassess | ActionResultDTO:
    repo = ChatRepository(db)
    row = lock_current(db, run)
    if row is None:
        raise RunLost(run.run_id)
    args = ReassessArgs.model_validate(action["args"])
    take = _owned_take_by_id(db, repo, row.user_id, args.take_id)
    if take is None:
        return close_action(db, row, action, failed(action, "NOT_FOUND", "기록을 찾을 수 없어요."))
    review = repo.list_ai_results(take.Take.id, ["REVIEW"])
    if not review:
        return close_action(db, row, action, failed(action, "VALIDATION_ERROR", NO_REVIEW))
    if repo.count_succeeded_actions(row.id, "REASSESS") > 0:
        return close_action(
            db,
            row,
            action,
            failed(action, "VALIDATION_ERROR", "다시 평가는 한 번에 한 번만 해요."),
        )
    try:
        before = build_review_request(db, take.Take.id).model_dump(mode="json")
        after = _changed_request(before, args.change)
    except ValueError, NonExistentTake:
        return close_action(db, row, action, failed(action, "VALIDATION_ERROR", NO_REVIEW))
    if not repo.charge_reassess(row.user_id, datetime.now(SEOUL).date(), REASSESS_DAILY_LIMIT):
        return close_action(
            db,
            row,
            action,
            failed(
                action,
                "AGENT_DAILY_LIMIT",
                "오늘은 다시 평가를 더 할 수 없지만 다른 질문은 계속할 수 있어요.",
            ),
        )
    payload = review[0].payload
    db.commit()
    return PreparedReassess(
        take_id=take.Take.id,
        review_revision=review[0].revision,
        official={"score": payload.get("score"), "dimensions": payload.get("dimensions")},
        before=before,
        after=after,
        before_digest=digest(before),
    )


def _owned_take_by_id(
    db: Session, repo: ChatRepository, user_id: uuid.UUID, take_id: str
) -> Row | None:
    try:
        take_uuid = uuid.UUID(take_id)
    except ValueError:
        return None
    take = repo.get_take_with_review(take_uuid)
    if take is None or PitchRepository(db).get_owned(take.Take.pitch_id, user_id) is None:
        return None
    return take


def _changed_request(before: dict[str, Any], change: dict[str, Any]) -> dict[str, Any]:
    after = copy.deepcopy(before)
    criteria = after["user_criteria"] or copy.deepcopy(EMPTY_CRITERIA)
    criteria.update(change)
    after["user_criteria"] = UserCriteriaDTO.model_validate(criteria).model_dump(mode="json")
    if {**before, "user_criteria": None} != {**after, "user_criteria": None}:
        raise ValueError("허용 칸 밖이 바뀌었다")
    return after


async def call_reassess(prepared: PreparedReassess) -> tuple[dict, dict] | None:
    try:
        async with asyncio.TaskGroup() as group:
            before = group.create_task(request_review_once(prepared.take_id, prepared.before))
            after = group.create_task(request_review_once(prepared.take_id, prepared.after))
    except Exception:
        return None
    return before.result(), after.result()


def finish_reassess(
    db: Session,
    run: ClaimedRun,
    action: dict[str, Any],
    prepared: PreparedReassess,
    replies: tuple[dict, dict] | None,
) -> ActionResultDTO:
    lock_or_cancel(db, run, set(), {str(prepared.take_id)})
    repo = ChatRepository(db)
    row = lock_current(db, run)
    if row is None:
        raise RunLost(run.run_id)
    review = repo.list_ai_results(prepared.take_id, ["REVIEW"])
    if (
        replies is None
        or _request_changed(db, prepared)
        or replies[0]["meta"].get("criteria_versions")
        != replies[1]["meta"].get("criteria_versions")
        or not review
        or review[0].revision != prepared.review_revision
    ):
        result = failed(
            action, "AGENT_UPSTREAM_ERROR", "다시 평가하지 못했으니 잠시 뒤 다시 시도해 주세요."
        )
    else:
        result = succeeded(
            action, {"before": replies[0], "after": replies[1], "official": prepared.official}
        )
    saved = repo.get_action(row.id, action["action_id"])
    saved.status = "SUCCEEDED" if result.status == "SUCCEEDED" else "FAILED"
    saved.result = {**result.model_dump(mode="json"), "review_revision": prepared.review_revision}
    saved.source_take_ids = [str(prepared.take_id)]
    db.commit()
    return result


def _request_changed(db: Session, prepared: PreparedReassess) -> bool:
    try:
        current = build_review_request(db, prepared.take_id).model_dump(mode="json")
    except ValueError, NonExistentTake:
        return True
    return digest(current) != prepared.before_digest
