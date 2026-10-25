"""리뷰 요청 조립 · AI 호출 · 결과 저장. 결과 · 미션 · 요약 · Take 완료는 한 트랜잭션이다."""

import logging
import time
import uuid

import httpx2
from sqlalchemy.orm import Session

from pitch_coach_backend.core.config import settings
from pitch_coach_backend.module.pitch.service import standard as standard_service
from pitch_coach_backend.module.take.ai_response import validate_review_reply
from pitch_coach_backend.module.take.dto import (
    MissionInputDTO,
    PreviousTakeDTO,
    ReviewRequestDTO,
    ReviewResponseDTO,
    TargetInfoDTO,
    UserCriteriaDTO,
)
from pitch_coach_backend.module.take.entity import Take
from pitch_coach_backend.module.take.exception import (
    InvalidReviewResponse,
    NonExistentTake,
    TakeNotReviewable,
)
from pitch_coach_backend.module.take.repository import TakeRepository

logger = logging.getLogger(__name__)


def _build_missions(take_repository: TakeRepository, take_id: uuid.UUID) -> list[MissionInputDTO]:
    mission_ids = take_repository.get_mission_ids_of_take(take_id)
    missions = take_repository.get_missions_by_ids(mission_ids)
    if len(missions) < len(mission_ids):
        logger.warning(
            "take %s: %d of %d missions not found",
            take_id,
            len(mission_ids) - len(missions),
            len(mission_ids),
        )
    return [
        MissionInputDTO(
            mission_id=mission.id,
            area=mission.area,
            slide_number=mission.slide_number,
            target=TargetInfoDTO(**mission.target),
        )
        for mission in missions
    ]


def _build_previous_takes(
    take_repository: TakeRepository, take_id: uuid.UUID, take_number: int
) -> list[PreviousTakeDTO]:
    return [
        PreviousTakeDTO(
            take_result=result,
            take_scores=coverage.get("take_scores") if coverage else None,
        )
        for result, coverage in take_repository.get_previous_take_results(take_id, take_number)
    ]


def _build_previous_recurring_issues(
    take_repository: TakeRepository, take_id: uuid.UUID, take_number: int
) -> list[dict]:
    previous_review = take_repository.get_previous_review_payload(take_id, take_number)
    return previous_review.get("recurring_issues", []) if previous_review else []


def _build_user_criteria(db: Session, take: Take) -> UserCriteriaDTO | None:
    criteria = standard_service.criteria_for_version(db, take.pitch_id, take.standard_version)
    return UserCriteriaDTO.model_validate(criteria) if criteria is not None else None


def build_review_request(db: Session, take_id: uuid.UUID) -> ReviewRequestDTO:
    take_repository = TakeRepository(db)
    take = take_repository.get_take(take_id)
    if take is None:
        raise NonExistentTake()
    take_number = take.take_number
    result = take_repository.get_take_infos(take_id, "TAKE_RESULT")
    coverage = take_repository.get_take_infos(take_id, "SCRIPT_COVERAGE")
    # 전체 실패 진단은 저장본에 보존하고 리뷰에는 채점 없음으로 보낸다.
    if coverage is not None and not coverage.get("evaluations"):
        coverage = None
    return ReviewRequestDTO(
        take_result=result,
        script_coverage=coverage,
        previous_takes=_build_previous_takes(take_repository, take_id, take_number),
        previous_recurring_issues=_build_previous_recurring_issues(
            take_repository, take_id, take_number
        ),
        missions=_build_missions(take_repository, take_id),
        user_criteria=_build_user_criteria(db, take),
    )


REVIEW_RETRY_DELAY_SEC = 1.0


def request_review(take_id: uuid.UUID, review_request: ReviewRequestDTO) -> ReviewResponseDTO:
    """AI 서버에 리뷰를 요청한다. 응답이 계약과 다르거나 다른 Take 의 것이면
    InvalidReviewResponse(502). 저장하다 DB 제약에 걸리기 전에 여기서 거른다."""
    request_id = uuid.uuid4().hex
    with httpx2.Client(timeout=30.0) as client:
        for attempt in range(2):
            try:
                response = client.post(
                    f"{settings.ai_base_url}/takes/review",
                    json=review_request.model_dump(mode="json"),
                    headers={"X-Request-ID": request_id},
                )
            except httpx2.HTTPError as exc:
                if attempt:
                    raise InvalidReviewResponse() from exc
            else:
                if response.status_code == 200:
                    try:
                        reply = response.json()
                    except ValueError as exc:
                        raise InvalidReviewResponse() from exc
                    return validate_review_reply(reply, take_id)
                retryable = response.status_code >= 500 or response.status_code in (408, 429)
                if attempt or not retryable:
                    raise InvalidReviewResponse()
            time.sleep(REVIEW_RETRY_DELAY_SEC)
    raise AssertionError("unreachable")


def save_review(
    db: Session,
    take_id: uuid.UUID,
    review_data: ReviewResponseDTO,
    *,
    request_id: uuid.UUID | None = None,
) -> bool:
    review_data = validate_review_reply(review_data.model_dump(mode="json"), take_id)
    take_repository = TakeRepository(db)
    if request_id is None or take_repository.lock_review(take_id, request_id) is None:
        db.rollback()
        return False
    take_repository.save_total_result(
        take_id,
        review_data.model_dump(mode="json", exclude={"meta"}),
        review_data.meta.model_dump(mode="json"),
    )
    take_repository.upsert_next_missions(
        [
            {
                "source_take_id": take_id,
                "area": mission.area,
                "slide_number": mission.slide_number,
                "target": mission.target.model_dump(exclude={"display"}),
                "description": mission.description,
                "priority": mission.priority,
                "reason": mission.reason,
                "status": "ACTIVE",
            }
            for mission in review_data.next_missions
        ]
    )
    for result in review_data.mission_results:
        take_repository.update_mission_result(
            result.mission_id, take_id, result.status, result.observed
        )
    take_repository.upsert_summary_review(take_id, review_data.overall_summary, review_data.score)
    take_repository.set_analysis_stage(take_id, "DONE")
    take_repository.complete_take(take_id, "DONE")
    db.commit()
    return True


def _fail_review(db: Session, take_id: uuid.UUID, request_id: uuid.UUID) -> bool:
    repository = TakeRepository(db)
    if repository.lock_review(take_id, request_id) is None:
        db.rollback()
        return False
    repository.set_analysis_stage(take_id, "DONE")
    repository.complete_take(take_id, "FAILED")
    db.commit()
    return True


def get_review_result(db: Session, take_id: uuid.UUID) -> ReviewResponseDTO:
    take_repository = TakeRepository(db)
    request_id = uuid.uuid4()
    if not take_repository.start_review(take_id, request_id):
        db.rollback()
        raise TakeNotReviewable()
    db.commit()

    try:
        review_request = build_review_request(db, take_id)
        db.commit()
        review_data = request_review(take_id, review_request)
        if not save_review(db, take_id, review_data, request_id=request_id):
            raise TakeNotReviewable()
    except Exception:
        db.rollback()
        # 성공·실패 모두 현재 작업만 확정한다. 다른 작업이 이어받았거나 Take가 삭제됐으면 버린다.
        if not _fail_review(db, take_id, request_id):
            raise TakeNotReviewable() from None
        raise

    return review_data
