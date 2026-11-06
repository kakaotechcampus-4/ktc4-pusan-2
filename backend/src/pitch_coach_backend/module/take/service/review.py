"""리뷰 요청 조립 · AI 호출 · 결과 저장. 결과 · 미션 · 요약 · Take 완료는 한 트랜잭션이다."""

import logging
import uuid

from sqlalchemy.orm import Session

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
    NonExistentTake,
)
from pitch_coach_backend.module.take.repository import TakeRepository, parse_mission_ids
from pitch_coach_backend.module.take.states import COVERAGE_KIND, DONE, FAILED, REVIEW, TAKE_RESULT

logger = logging.getLogger(__name__)


def _build_missions(take_repository: TakeRepository, take: Take) -> list[MissionInputDTO]:
    mission_ids = parse_mission_ids(take.mission_ids)
    missions = take_repository.get_missions_by_ids(mission_ids)
    if len(missions) < len(mission_ids):
        logger.warning(
            "take=%s %d of %d missions not found",
            take.id,
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
    take_repository: TakeRepository, pitch_id: uuid.UUID, take_number: int
) -> list[PreviousTakeDTO]:
    return [
        PreviousTakeDTO(
            take_result=result,
            take_scores=coverage.get("take_scores") if coverage else None,
        )
        for result, coverage in take_repository.get_previous_take_results(pitch_id, take_number)
    ]


def _build_previous_recurring_issues(
    take_repository: TakeRepository, pitch_id: uuid.UUID, take_number: int
) -> list[dict]:
    previous_review = take_repository.get_previous_review_payload(pitch_id, take_number)
    return previous_review.get("recurring_issues", []) if previous_review else []


def _build_user_criteria(db: Session, take: Take) -> UserCriteriaDTO | None:
    criteria = standard_service.criteria_for_version(db, take.pitch_id, take.standard_version)
    return UserCriteriaDTO.model_validate(criteria) if criteria is not None else None


def build_review_request(db: Session, take_id: uuid.UUID) -> ReviewRequestDTO:
    take_repository = TakeRepository(db)
    take = take_repository.get_take(take_id)
    if take is None:
        raise NonExistentTake()
    payloads = take_repository.get_ai_payloads(take_id, (TAKE_RESULT, COVERAGE_KIND))
    coverage = payloads.get(COVERAGE_KIND)
    # 전체 실패 진단은 저장본에 보존하고 리뷰에는 채점 없음으로 보낸다.
    if coverage is not None and not coverage.get("evaluations"):
        coverage = None
    return ReviewRequestDTO(
        take_result=payloads.get(TAKE_RESULT),
        script_coverage=coverage,
        previous_takes=_build_previous_takes(take_repository, take.pitch_id, take.take_number),
        previous_recurring_issues=_build_previous_recurring_issues(
            take_repository, take.pitch_id, take.take_number
        ),
        missions=_build_missions(take_repository, take),
        user_criteria=_build_user_criteria(db, take),
    )


def claim_review(
    db: Session, take_id: uuid.UUID, request_id: uuid.UUID, *, takeover: bool = False
) -> uuid.UUID | None:
    take_repository = TakeRepository(db)
    if not take_repository.start_review(take_id, request_id, takeover=takeover):
        db.rollback()
        return None
    pitch_id = take_repository.get_pitch_id_of_take(take_id)
    db.commit()
    return pitch_id


def save_review(
    db: Session,
    take_id: uuid.UUID,
    review_data: ReviewResponseDTO,
    *,
    request_id: uuid.UUID,
) -> bool:
    review_data = validate_review_reply(review_data.model_dump(mode="json"), take_id)
    take_repository = TakeRepository(db)
    if take_repository.lock_review(take_id, request_id) is None:
        db.rollback()
        return False
    take_repository.save_ai_result(
        take_id,
        REVIEW,
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
    take_repository.set_analysis_stage(take_id, DONE)
    take_repository.complete_take(take_id, DONE)
    db.commit()
    return True


def fail_review(db: Session, take_id: uuid.UUID, request_id: uuid.UUID) -> bool:
    repository = TakeRepository(db)
    if repository.lock_review(take_id, request_id) is None:
        db.rollback()
        return False
    repository.set_analysis_stage(take_id, DONE)
    repository.complete_take(take_id, FAILED)
    db.commit()
    return True
