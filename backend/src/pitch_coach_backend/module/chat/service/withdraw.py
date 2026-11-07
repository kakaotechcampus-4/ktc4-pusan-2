"""회원 탈퇴 때 그 사용자의 끝나지 않은 대화 실행 정리(chat-agent.md 6-13).

실행 · 운영 기록(`chat_run_logs`)은 회원을 지우면 CASCADE 로 같이 사라져 `CANCELLED` 로 적지
않는다. 사용자 없는 서비스 하루 사용량 행만 남으므로, AI 에 아직 보내지 않은 실행의 예약은 사용자
취소와 같이 돌려준다.
"""

import uuid

from sqlalchemy.orm import Session

from pitch_coach_backend.module.chat.repository import ChatRepository
from pitch_coach_backend.module.chat.service.run import release_unsent


def release_user_runs(db: Session, user_id: uuid.UUID) -> list[uuid.UUID]:
    """회원을 지우기 전에 부른다. 커밋하지 않는다 — 회원을 지우는 커밋에 같이 묶인다.

    끊을 실행 id 를 돌려준다. 턴은 지우기를 커밋한 뒤 `turn_runner.cancel` 로 끊는다. 실행을 잠근 뒤
    보낸 호출이 있는지 보므로, 턴 실행기가 첫 호출을 보내며 예약을 보낸 수로 바꾼 것과 겹쳐도 두 번
    돌려주지 않는다.
    """
    repo = ChatRepository(db)
    runs = repo.lock_unfinished_runs_of(user_id)
    for run in runs:
        release_unsent(repo, run)
    return [run.id for run in runs]
