"""코치 연동의 도메인 작업. 기능별 PR에서 단계적으로 연결한다."""

import uuid

from sqlalchemy.orm import Session

from pitch_coach_backend.module.pitch import service as pitch_service
from pitch_coach_backend.module.take.coach_repository import CoachRepository


def start_take(db: Session, take_id: uuid.UUID) -> bool:
    """첫 실시간 연결이 붙었다. `READY` 면 `RUNNING` 으로 바꾸고, 이 Take 에 쓸 값을 고정하고
    커밋한다. 바꿨으면 True. 이미 `RUNNING` 이거나(다시 붙은 연결) 끝났으면 아무것도 바꾸지 않는다.

    고정하는 값 (1-3 1번): 실은 미션 id 목록(`CoachRepository.assign_missions`) · 최신 평가 기준
    버전 · 원자료 사용 동의.

    발표 행을 먼저 잠근다. 새 Take 를 만드는 요청도 발표 행을 잠근 뒤 `READY` Take 를 끝내므로,
    순서가 거꾸로면 둘이 서로를 기다린다.
    """
    repo = CoachRepository(db)
    pitch_id = repo.pitch_id_of(take_id)
    if pitch_id is None:
        return False
    pitch_service.lock_for_new_version(db, pitch_id)
    take_number = repo.mark_started(take_id)
    if take_number is None:
        db.commit()
        return False
    mission_ids = repo.assign_missions(take_id, pitch_id, before=take_number)
    repo.fix_started_values(
        take_id,
        mission_ids=mission_ids,
        standard_version=pitch_service.latest_analysis_version(db, pitch_id),
    )
    db.commit()
    return True
