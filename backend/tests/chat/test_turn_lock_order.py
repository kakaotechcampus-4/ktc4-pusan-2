"""턴을 끝내는 저장이 피치 삭제와 겹쳐도 서로 기다리지 않는다(잠그는 순서는 waiting.py).

피치 삭제는 피치 행을 잠근 뒤 `scope_pitch_id` 의 CASCADE 로 그 발표의 대화를 지운다. 턴 저장이
대화를 먼저 잠그고 보낸 문장의 근거 피치를 나중에 잠그면 둘이 서로를 기다린다.
실제 커넥션 두 개로 삭제가 피치를 잡은 사이에 턴 저장을 시작한다.
"""

import time
import uuid
from collections.abc import Callable, Generator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import Engine, delete, select, text
from sqlalchemy.orm import Session

from pitch_coach_backend.module.chat.ai_client import TurnFailure, TurnFinal
from pitch_coach_backend.module.chat.entity import ChatConversation, ChatRun, ChatRunEvent
from pitch_coach_backend.module.chat.service import turn as turn_service
from pitch_coach_backend.module.chat.service.run import ClaimedRun, claimed
from pitch_coach_backend.module.pitch.entity import Pitch
from pitch_coach_backend.module.user.entity import User

# 보낸 문장과 다른 final. 턴 저장이 보낸 문장으로 끝내는 길(`_end_with_sent_claims`)로 간다
DIFFERENT_FINAL = TurnFinal(
    view=None,  # type: ignore[arg-type]  # 저장 쪽은 data 만 읽는다
    data={"status": "DONE", "message": {"text": "", "claims": []}, "evidence": []},
    diagnostics=None,
)

ENDINGS: dict[str, Callable[[Session, ClaimedRun], Any]] = {
    "fail": lambda db, run: turn_service.fail(db, run, TurnFailure("AGENT_UPSTREAM_ERROR")),
    "finish": lambda db, run: turn_service.finish(db, run, 1, DIFFERENT_FINAL),
    "wait_for_action": lambda db, run: turn_service.wait_for_action(db, run, 1, DIFFERENT_FINAL),
}


@pytest.fixture
def scoped_turn(engine: Engine) -> Generator[tuple[uuid.UUID, ClaimedRun]]:
    """발표 범위의 대화에서 그 발표를 근거로 문장 하나를 보낸 실행. 실제로 커밋한다."""
    now = datetime.now(UTC)
    with Session(engine, expire_on_commit=False) as db:
        user = User(email=f"{uuid.uuid4()}@example.com", name="삭제와 겹친 턴")
        db.add(user)
        db.flush()
        pitch = Pitch(user_id=user.id, title="졸업 발표", time_limit_sec=300)
        db.add(pitch)
        db.flush()
        conversation = ChatConversation(user_id=user.id, title="", scope_pitch_id=pitch.id)
        db.add(conversation)
        db.flush()
        run = ChatRun(
            conversation_id=conversation.id,
            user_id=user.id,
            first_trigger="USER_MESSAGE",
            status="RUNNING",
            input={"user_message": "어땠어?"},
            turn_calls=[{"turn_call": 1, "memory_version": 0}],
            accepted_at=now,
            deadline_at=now + timedelta(minutes=2),
        )
        db.add(run)
        db.flush()
        db.add(
            ChatRunEvent(
                run_id=run.id,
                event_seq=1,
                event="claim",
                data={
                    "claim": {"kind": "OBSERVATION", "text": "말이 빨랐어요"},
                    "evidence": [{"pitch_id": str(pitch.id)}],
                },
            )
        )
        db.commit()
        user_id, pitch_id, claimed_run = user.id, pitch.id, claimed(run)
    try:
        yield pitch_id, claimed_run
    finally:
        with Session(engine) as db:
            db.execute(delete(User).where(User.id == user_id))
            db.commit()


def _wait_for_lock(engine: Engine, pid: int) -> None:
    deadline = time.monotonic() + 5
    with engine.connect() as watcher:
        while time.monotonic() < deadline:
            waiting = watcher.scalar(
                text("SELECT wait_event_type = 'Lock' FROM pg_stat_activity WHERE pid = :pid"),
                {"pid": pid},
            )
            # 한 트랜잭션 안에서는 pg_stat_activity 값이 그대로라 끊고 다시 읽는다
            watcher.rollback()
            if waiting:
                return
            time.sleep(0.01)
    pytest.fail("턴 저장이 피치 잠금을 기다리지 않았다")


@pytest.mark.parametrize("ending", ENDINGS)
def test_ending_a_turn_while_its_pitch_is_deleted_does_not_deadlock(
    engine: Engine, scoped_turn: tuple[uuid.UUID, ClaimedRun], ending: str
) -> None:
    pitch_id, run = scoped_turn
    pids: list[int] = []

    def end_turn() -> Any:
        with Session(engine, autoflush=False, expire_on_commit=False) as db:
            pids.append(db.scalar(text("SELECT pg_backend_pid()")))
            return ENDINGS[ending](db, run)

    with Session(engine) as deleting, ThreadPoolExecutor(max_workers=1) as pool:
        # 피치 삭제처럼 피치 행을 먼저 잠근다(PitchRepository.lock_for_delete)
        deleting.execute(select(Pitch.id).where(Pitch.id == pitch_id).with_for_update())
        pending = pool.submit(end_turn)
        while not pids and not pending.done():
            time.sleep(0.01)
        _wait_for_lock(engine, pids[0])
        # 잠금 순서가 거꾸로면 여기서 대화를 기다리다 교착으로 한쪽이 실패한다
        deleting.execute(delete(Pitch).where(Pitch.id == pitch_id))
        deleting.commit()
        result = pending.result(timeout=10)

    # 대화와 실행은 CASCADE 로 사라졌다. 턴 저장은 실행을 놓쳤다고 보고 끝낸다
    assert not result
    with Session(engine) as check:
        assert check.get(ChatRun, run.run_id) is None
