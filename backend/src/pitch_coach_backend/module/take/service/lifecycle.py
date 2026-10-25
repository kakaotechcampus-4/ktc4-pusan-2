"""Take 상태 바꾸기 — 시작 · 끝내기 · 떠남 · 창 닫힘 · 시작하지 않은 Take 정리 · 진행 중 Take 조회.

realtime · HTTP 요청이 부른다. `service/take.py` 를 import 하지 않는다.
"""

import uuid
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from pitch_coach_backend.module.take import coach_service
from pitch_coach_backend.module.take.exception import NonExistentTake
from pitch_coach_backend.module.take.repository import TakeRepository, last_active_at


def close_take_service(db: Session, take_id: uuid.UUID) -> tuple[str, bool]:
    """창을 닫았다 다시 연 FE 가 Take 를 실패(`FAILED` · `CLOSED`)로 끝낸다. (지금 상태, 바꿨나).

    창을 닫은 Take 는 이어 가지 않는다. 진행 중(`READY` · `RUNNING`)일 때만 바꾸고, 이미 끝났으면
    그대로 두고 지금 상태를 돌려준다 — FE 는 `ANALYZING` 이면 끝내기가 이미 닿은 것으로 본다.
    끝난 시각은 마지막 활동 시각이다 (앱이 다시 뜰 때 떠난 Take 를 재는 기준과 같다).
    """
    take_repository = TakeRepository(db)
    closed = take_repository.mark_abandoned(take_id, last_active_at(), end_reason="CLOSED")
    db.commit()
    take = take_repository.get_take(take_id)
    if take is None:
        raise NonExistentTake()
    return take.status, closed


def mark_running(db: Session, take_id: uuid.UUID) -> bool:
    """첫 실시간 연결이 붙었다. `READY` 면 `RUNNING` 으로 바꾸고, 이 Take 에 쓸 값(실은 미션 ·
    평가 기준 버전 · 원자료 사용 동의)을 고정한 뒤 커밋한다. 바꿨으면 True
    (`coach_service.start_take`).
    """
    return coach_service.start_take(db, take_id)


def mark_ended(
    db: Session,
    take_id: uuid.UUID,
    end_reason: str | None = None,
    utterance_ends: list[int] | None = None) -> bool:
    """Take 를 끝냈다. `RUNNING` 이면 상태를 `ANALYZING`, 분석 단계를 `FINALIZING` 으로 바꾸고
    커밋한다.

    WS stop 이 부르고, 발표 중에 연결이 끊겨 STT 없이 끝낸 Take 는 Take PUT(`ended_at`)이
    부른다. BE 가 자동으로 끝냈으면 `end_reason` 에 이유를 남긴다 (처음 적은 이유가 남는다).
    떠난 것으로 끝낸 Take 는 되살린다 (STT 없이 발표를 이어 간 FE 의 끝내기다).
    `utterance_ends` 는 Take 동안 메모리에 들고 있던 문장 끝 신호다 — 있으면 같이 쓴다
    (`docs/ai/architecture.md` 1-3 3번 ③).
    Take 가 그 사이 지워졌거나 이미 끝났으면 아무것도 안 하고 False.
    """
    marked = TakeRepository(db).mark_ended(take_id, end_reason, utterance_ends)
    db.commit()
    return marked


def mark_abandoned(
    db: Session,
    take_id: uuid.UUID,
    ended_at: datetime,
    end_reason: str = "ABANDONED",
    utterance_ends: list[int] | None = None) -> bool:
    """사용자가 떠난 Take 를 `FAILED` 로 바꾸고 이유(`ABANDONED` · `CLOSED`)를 적고 커밋한다.

    연결 없이 정리된 스트림에 정해진 시간 동안 다시 붙지 않으면 부른다. `ended_at` 은 마지막으로
    연결이 끊긴 시각이다. Take 동안 모은 문장 끝 신호가 있으면 같이 쓴다 — 끝내기 PUT 이 되살리면
    replay 가 쓴다. Take 가 그 사이 지워졌거나 이미 끝났으면 아무것도 안 하고 False.
    """
    marked = TakeRepository(db).mark_abandoned(take_id, ended_at, end_reason, utterance_ends)
    db.commit()
    return marked


def running_takes(db: Session) -> list[tuple[uuid.UUID, datetime]]:
    """`RUNNING` 인 Take 와 마지막으로 활동한 시각. 앱이 다시 뜰 때 떠난 Take 를 찾는다."""
    return TakeRepository(db).running_takes_last_active()


ABANDON_AFTER_SEC = 5 * 60.0


# 시작하지 않은 Take 를 끝내는 기준. Take 는 장치 점검에서 시작을 누를 때 만들고 곧바로 실시간
# 연결이 붙으므로 보통 몇 초면 `RUNNING` 이 된다. 권한 창 · 느린 연결을 넉넉히 기다린다
READY_EXPIRE_AFTER = timedelta(minutes=10)


def abandon_stale_ready_takes(db: Session) -> int:
    """만든 지 `READY_EXPIRE_AFTER` 가 지나도 `READY` 인 Take 를 `FAILED`(`ABANDONED`)로 끝내고
    커밋한다. 끝낸 수를 돌려준다."""
    abandoned = TakeRepository(db).abandon_stale_ready_takes(READY_EXPIRE_AFTER)
    db.commit()
    return abandoned
