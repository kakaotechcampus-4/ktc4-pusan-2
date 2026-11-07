"""하루 사용량 선점과 실행 근거 모으기. 여러 곳이 같은 함수를 쓰므로 함수 자체를 확인한다."""

import uuid
from datetime import date

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from pitch_coach_backend.module.chat.entity import ChatDailyUsage
from pitch_coach_backend.module.chat.repository import charge_daily
from pitch_coach_backend.module.chat.service.stale import run_source_ids

DAY = date(2026, 11, 7)
COUNTERS = ("turns", "reassessments", "goal_checks")


def _usage(db_session: Session, user_id: uuid.UUID, day: date = DAY) -> dict[str, int]:
    row = db_session.scalar(
        select(ChatDailyUsage).where(ChatDailyUsage.user_id == user_id, ChatDailyUsage.day == day)
    )
    assert row is not None
    return {column: getattr(row, column) for column in COUNTERS}


@pytest.mark.parametrize("column", COUNTERS)
def test_charge_counts_only_its_own_column_up_to_the_limit(
    db_session: Session, user_id: uuid.UUID, column: str
) -> None:
    assert [charge_daily(db_session, column, user_id, DAY, 2) for _ in range(3)] == [
        True,
        True,
        False,
    ]
    assert _usage(db_session, user_id) == {name: 2 if name == column else 0 for name in COUNTERS}


def test_charges_of_different_columns_share_one_row(
    db_session: Session, user_id: uuid.UUID
) -> None:
    for column in COUNTERS:
        assert charge_daily(db_session, column, user_id, DAY, 1)
    assert not charge_daily(db_session, "turns", user_id, DAY, 1)
    # 다른 날은 새로 센다
    assert charge_daily(db_session, "turns", user_id, date(2026, 11, 8), 1)
    assert _usage(db_session, user_id) == dict.fromkeys(COUNTERS, 1)


def test_run_sources_merge_read_sources_and_action_takes() -> None:
    pitches, takes = run_source_ids(
        {"pitches": ["p1"], "takes": ["t1"]}, [["t2"], None, ["t1", "t3"]]
    )
    assert (pitches, takes) == ({"p1"}, {"t1", "t2", "t3"})


@pytest.mark.parametrize("read_sources", [None, {}, {"pitches": None, "takes": None}, []])
def test_run_sources_accept_missing_read_sources(read_sources: object) -> None:
    assert run_source_ids(read_sources, [["t1"]]) == (set(), {"t1"})
