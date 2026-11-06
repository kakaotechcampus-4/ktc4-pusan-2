import asyncio
import copy
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import httpx
from sqlalchemy.orm import Session

from pitch_coach_backend.module.chat.ai_client import ReviewTakeMismatch, request_review_once
from pitch_coach_backend.module.chat.ai_response import ReassessArgs
from pitch_coach_backend.module.chat.dto import ActionResultDTO
from pitch_coach_backend.module.chat.repository import ChatRepository
from pitch_coach_backend.module.chat.service.owned import owned_take
from pitch_coach_backend.module.chat.service.request import run_scope
from pitch_coach_backend.module.chat.service.result import (
    UNCONFIRMED,
    close_action,
    failed,
    save_result,
    succeeded,
)
from pitch_coach_backend.module.chat.service.run import (
    SEOUL,
    ClaimedRun,
    RunLost,
    digest,
    lock_current,
    lock_or_cancel,
)
from pitch_coach_backend.module.take.dto import UserCriteriaDTO
from pitch_coach_backend.module.take.exception import NonExistentTake
from pitch_coach_backend.module.take.service.review import build_review_request

logger = logging.getLogger(__name__)

REASSESS_DAILY_LIMIT = 30
EMPTY_CRITERIA = {"display_criteria": [], "targets": [], "script_used": None}
NO_REVIEW = "이 연습은 다시 평가할 자료가 부족해요."
REVIEW_FAILED = "REVIEW_FAILED"
REVIEW_TAKE_MISMATCH = "REVIEW_TAKE_MISMATCH"


@dataclass(frozen=True)
class PreparedReassess:
    take_id: uuid.UUID
    review_revision: int
    official: dict[str, Any]
    before: dict[str, Any]
    after: dict[str, Any]
    before_digest: str


def prepare_reassess(
    db: Session, run: ClaimedRun, action: dict[str, Any], args: ReassessArgs
) -> PreparedReassess | ActionResultDTO:
    repo = ChatRepository(db)
    row = lock_current(db, run)
    if row is None:
        raise RunLost(run.run_id)
    take = owned_take(db, row.user_id, args.take_id)
    conversation = repo.get_conversation(row.conversation_id)
    if take is None or take.Take.pitch_id != run_scope(row, conversation):
        return close_action(db, row, action, failed(action, "NOT_FOUND", "기록을 찾을 수 없어요."))
    review = repo.list_ai_results(take.Take.id, ["REVIEW"])
    if not review:
        return close_action(db, row, action, failed(action, "NO_OFFICIAL_REVIEW", NO_REVIEW))
    if repo.count_succeeded_actions(row.id, "REASSESS") > 0:
        return close_action(db, row, action, failed(action, "REASSESS_PER_TURN", UNCONFIRMED))
    try:
        before = build_review_request(db, take.Take.id).model_dump(mode="json")
    except ValueError, NonExistentTake:
        return close_action(db, row, action, failed(action, "NO_OFFICIAL_REVIEW", NO_REVIEW))
    try:
        after = _changed_request(before, args.change)
    except ValueError:
        return close_action(db, row, action, failed(action, "INVALID_CHANGE", UNCONFIRMED))
    if not repo.charge_reassess(row.user_id, datetime.now(SEOUL).date(), REASSESS_DAILY_LIMIT):
        return close_action(
            db,
            row,
            action,
            failed(
                action,
                "REASSESS_DAILY_LIMIT",
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


def _changed_request(before: dict[str, Any], change: dict[str, Any]) -> dict[str, Any]:
    after = copy.deepcopy(before)
    criteria = after["user_criteria"] or copy.deepcopy(EMPTY_CRITERIA)
    criteria.update(change)
    after["user_criteria"] = UserCriteriaDTO.model_validate(criteria).model_dump(mode="json")
    if {**before, "user_criteria": None} != {**after, "user_criteria": None}:
        raise ValueError("허용 칸 밖이 바뀌었다")
    return after


async def call_reassess(prepared: PreparedReassess) -> tuple[dict, dict] | str:
    try:
        async with asyncio.TaskGroup() as group:
            before = group.create_task(request_review_once(prepared.take_id, prepared.before))
            after = group.create_task(request_review_once(prepared.take_id, prepared.after))
    except ExceptionGroup as failure:
        # 응답 원문이 실릴 수 있어 예외 문구는 남기지 않고 종류(와 HTTP 상태)만 남긴다
        logger.warning(
            "다시 평가 호출 실패 take=%s 종류=%s", prepared.take_id, _failure_kinds(failure)
        )
        return REVIEW_TAKE_MISMATCH if failure.subgroup(ReviewTakeMismatch) else REVIEW_FAILED
    return before.result(), after.result()


def _failure_kinds(failure: BaseExceptionGroup) -> list[str]:
    kinds = []
    for exc in failure.exceptions:
        if isinstance(exc, BaseExceptionGroup):
            kinds.extend(_failure_kinds(exc))
        elif isinstance(exc, httpx.HTTPStatusError):
            kinds.append(f"{type(exc).__name__}({exc.response.status_code})")
        else:
            kinds.append(type(exc).__name__)
    return sorted(kinds)


def finish_reassess(
    db: Session,
    run: ClaimedRun,
    action: dict[str, Any],
    prepared: PreparedReassess,
    replies: tuple[dict, dict] | str,
) -> ActionResultDTO:
    lock_or_cancel(db, run, set(), {str(prepared.take_id)})
    repo = ChatRepository(db)
    row = lock_current(db, run)
    if row is None:
        raise RunLost(run.run_id)
    review = repo.list_ai_results(prepared.take_id, ["REVIEW"])
    if replies == REVIEW_FAILED:
        result = failed(action, REVIEW_FAILED, "다시 평가하지 못했으니 잠시 뒤 다시 시도해 주세요.")
    elif replies == REVIEW_TAKE_MISMATCH:
        result = failed(action, REVIEW_TAKE_MISMATCH, UNCONFIRMED)
    elif (
        _request_changed(db, prepared)
        or not review
        or review[0].revision != prepared.review_revision
    ):
        result = failed(action, "REVIEW_REQUEST_CHANGED", UNCONFIRMED)
    elif replies[0]["meta"].get("criteria_versions") != replies[1]["meta"].get("criteria_versions"):
        result = failed(action, "RULES_DIFFER", "평가 규칙이 달라 같은 조건으로 비교할 수 없어요.")
    else:
        result = succeeded(
            action, {"before": replies[0], "after": replies[1], "official": prepared.official}
        )
    saved = repo.get_action(row.id, action["action_id"])
    save_result(saved, result, review_revision=prepared.review_revision)
    saved.source_take_ids = [str(prepared.take_id)]
    db.commit()
    return result


def _request_changed(db: Session, prepared: PreparedReassess) -> bool:
    try:
        current = build_review_request(db, prepared.take_id).model_dump(mode="json")
    except ValueError, NonExistentTake:
        return True
    return digest(current) != prepared.before_digest
