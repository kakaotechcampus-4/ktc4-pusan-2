"""finalize 가 실패한 Take 를 관리자가 정해 다시 만든다 (coach-agent.md 7절 수동 복구).

Take 결과를 기다리는 중으로 되돌리면 기존 finalize 가 원자료(replay)로 이어 간다.
"""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete
from sqlalchemy.orm import Session

from pitch_coach_backend.coach import finalize
from pitch_coach_backend.coach.store import DbCoachStore
from pitch_coach_backend.core.tasks import TaskRegistry
from pitch_coach_backend.module.take import coach_service
from pitch_coach_backend.module.take.entity import Take, TakeMetricSecond
from tests.coach_fakes import TAKE_ID, FakeClient, FakeStore, source, take_result
from tests.coach_storage_seed import (  # noqa: F401
    finalizing,
    make_take,
    pitch,
    presentation,
    script,
    take_parts,
    user,
)
from tests.test_coach_finalize_storage import finalize_reply


def _failed(db: Session, take_parts, *, consent: bool = False, ended_days_ago: float = 1) -> Take:
    """finalize 가 끝내 실패한 Take. 1초 원자료가 남아 있다."""
    take = finalizing(db, make_take(db, *take_parts, 1), consent=consent)
    assert coach_service.fail_take_result(db, take.id) is True
    take.ended_at = datetime.now(UTC) - timedelta(days=ended_days_ago)
    db.commit()
    return take


def _reopen(db: Session, take: Take) -> str | None:
    return coach_service.reopen_failed_finalize(db, take.id, datetime.now(UTC))


def test_failed_take_goes_back_to_waiting_for_its_result(db_session: Session, take_parts):
    take = _failed(db_session, take_parts)

    assert _reopen(db_session, take) == "REOPENED"

    db_session.refresh(take)
    assert (take.status, take.analysis_stage) == ("ANALYZING", "FINALIZING")
    assert take.analysis_stage_at is not None
    # 앱이 다시 뜰 때도 이어 간다
    assert coach_service.finalizing_takes(db_session) == [take.id]
    # 한 번 되돌린 Take 는 다시 되돌리지 않는다 (두 번 눌러도 finalize 가 한 번 뜬다)
    assert _reopen(db_session, take) == "NOT_FAILED"


@pytest.mark.parametrize(
    "status, stage",
    [
        ("ANALYZING", "FINALIZING"),
        ("ANALYZING", "SCORING"),
        ("COMPLETED", "DONE"),
        # 떠난 Take. 사용자가 그만둔 연습이라 되돌리지 않는다
        ("FAILED", None),
    ],
)
def test_only_a_take_whose_finalize_failed_is_reopened(
    db_session: Session, take_parts, status: str, stage: str | None
):
    take = _failed(db_session, take_parts)
    take.status = status
    take.analysis_stage = stage
    db_session.commit()

    assert _reopen(db_session, take) == "NOT_FAILED"

    db_session.refresh(take)
    assert (take.status, take.analysis_stage) == (status, stage)


def test_raw_data_without_consent_is_reopened_only_within_seven_days(
    db_session: Session, take_parts
):
    take = _failed(db_session, take_parts, ended_days_ago=7.01)

    # 정리 작업이 아직 지우지 않았어도 기한이 지났으면 되돌리지 않는다
    assert _reopen(db_session, take) == "EXPIRED"

    db_session.refresh(take)
    assert take.status == "FAILED"


def test_consented_raw_data_has_no_deadline(db_session: Session, take_parts):
    take = _failed(db_session, take_parts, consent=True, ended_days_ago=30)

    assert _reopen(db_session, take) == "REOPENED"


def test_take_without_raw_data_is_not_reopened(db_session: Session, take_parts):
    take = _failed(db_session, take_parts)
    db_session.execute(delete(TakeMetricSecond).where(TakeMetricSecond.take_id == take.id))
    db_session.commit()

    assert _reopen(db_session, take) == "NO_RAW_DATA"

    db_session.refresh(take)
    assert take.status == "FAILED"


def test_unknown_take_is_none(db_session: Session):
    assert coach_service.reopen_failed_finalize(db_session, uuid.uuid4(), datetime.now(UTC)) is None


def test_reopened_take_is_left_alone_by_the_raw_data_purge(db_session: Session, take_parts):
    take = _failed(db_session, take_parts, ended_days_ago=6.99)
    assert _reopen(db_session, take) == "REOPENED"

    # 기한을 넘긴 뒤 정리 작업이 돌아도 FAILED 가 아니라 지우지 않는다
    assert coach_service.purge_expired_raw(db_session, datetime.now(UTC) + timedelta(days=1)) == 0


@pytest.mark.anyio
async def test_reopened_take_gets_its_result_from_the_raw_data(db_session: Session, take_parts):
    take = _failed(db_session, take_parts)
    assert _reopen(db_session, take) == "REOPENED"
    client = FakeClient()
    client.finalize_replies = [finalize_reply(take)]

    await finalize.run(
        take.id, None, client=client, store=DbCoachStore(session_factory=lambda: db_session)
    )

    # 코치 상태가 없으니 처음부터 원자료를 붙여 부른다
    [request] = client.finalized
    assert request["replay"] is not None
    # 저장소가 부를 때마다 세션을 닫아 Take 를 다시 읽는다
    saved = db_session.get(Take, take.id)
    assert (saved.status, saved.analysis_stage) == ("ANALYZING", "SCORING")


def test_restart_spawns_finalize_without_a_snapshot(monkeypatch):
    """관리자 콘솔의 hook 은 코치 상태 없이 finalize 를 띄운다 — 처음부터 replay 를 붙인다."""
    spawned: list[tuple] = []
    monkeypatch.setattr(
        finalize, "spawn", lambda take_id, snapshot, **deps: spawned.append((take_id, snapshot))
    )
    take_id = uuid.uuid4()

    finalize.CoachFinalizeRecovery().restart(take_id)

    assert spawned == [(take_id, None)]


@pytest.mark.anyio
async def test_restart_follows_the_finalize_that_is_still_ending(monkeypatch):
    """앞 finalize 가 Take 를 FAILED 로 적고 아직 끝나는 중이면 건너뛰지 않고 끝난 뒤 띄운다."""
    monkeypatch.setattr(finalize, "_running", TaskRegistry())
    marked, release = asyncio.Event(), asyncio.Event()

    class EndingSlowly(FakeStore):
        async def fail_take_result(self, take_id) -> bool:
            await super().fail_take_result(take_id)
            marked.set()
            await release.wait()
            return True

    client, store = FakeClient(), EndingSlowly(source=source())
    # 앞 작업은 Take 결과를 못 만들고, 되돌린 뒤의 작업은 만든다
    client.finalize_replies = [None, take_result(replayed=True)]
    ending = finalize.spawn(TAKE_ID, None, client=client, store=store)
    await asyncio.wait_for(marked.wait(), 1)

    finalize.restart(TAKE_ID, client=client, store=store)
    assert len(client.finalized) == 1

    release.set()
    await ending
    await asyncio.gather(*finalize._running.tasks())

    assert len(client.finalized) == 2
    assert client.finalized[1]["replay"] is not None
    assert len(store.saved) == 1


@pytest.mark.anyio
async def test_restart_does_not_follow_a_finalize_stopped_by_shutdown(monkeypatch):
    """앱을 내려 앞 작업이 끊겼으면 띄우지 않는다. Take 는 FINALIZING 으로 남아 다시 뜰 때
    이어 간다."""
    monkeypatch.setattr(finalize, "_running", TaskRegistry())
    entered = asyncio.Event()

    class Hanging(FakeClient):
        async def finalize(self, request):
            self.finalized.append(request)
            entered.set()
            await asyncio.Event().wait()

    client, store = Hanging(), FakeStore(source=source())
    finalize.spawn(TAKE_ID, None, client=client, store=store)
    await asyncio.wait_for(entered.wait(), 1)
    finalize.restart(TAKE_ID, client=client, store=store)

    await finalize._running.stop_all(1)

    assert len(client.finalized) == 1
    assert len(finalize._running) == 0
