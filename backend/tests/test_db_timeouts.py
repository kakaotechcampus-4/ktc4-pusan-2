"""DB 연결의 잠금 · 문장 대기 상한 (core/database.py `create_app_engine`)."""

import pytest
from sqlalchemy import Engine, text
from sqlalchemy import exc as sa_exc

from pitch_coach_backend.core.config import settings
from pitch_coach_backend.core.database import create_app_engine, is_transient_db_error


@pytest.fixture
def timeout_engine(engine: Engine):
    app_engine = create_app_engine(
        engine.url.render_as_string(hide_password=False),
        lock_timeout_ms=200,
        statement_timeout_ms=300,
    )
    yield app_engine
    app_engine.dispose()


def test_connections_carry_configured_timeouts(timeout_engine: Engine) -> None:
    with timeout_engine.connect() as conn:
        assert conn.execute(text("SHOW lock_timeout")).scalar() == "200ms"
        assert conn.execute(text("SHOW statement_timeout")).scalar() == "300ms"


def test_default_limits_come_from_settings(engine: Engine) -> None:
    """값을 넘기지 않으면 설정값을 건다. 앱 엔진(`database.engine`)이 이렇게 만들어진다.

    앱 엔진에 직접 붙으면 테스트 DB 가 아니라 DATABASE_URL 의 DB 로 간다. 같은 함수로 테스트 DB
    엔진을 만들어 본다.
    """
    default_engine = create_app_engine(engine.url.render_as_string(hide_password=False))
    sql = text("SELECT setting::int FROM pg_settings WHERE name = :name")
    try:
        with default_engine.connect() as conn:
            lock_ms = conn.execute(sql, {"name": "lock_timeout"}).scalar()
            statement_ms = conn.execute(sql, {"name": "statement_timeout"}).scalar()
    finally:
        default_engine.dispose()

    assert lock_ms == settings.db_lock_timeout_ms
    assert statement_ms == settings.db_statement_timeout_ms


def test_waiting_for_a_held_lock_ends_as_transient_error(
    engine: Engine, timeout_engine: Engine
) -> None:
    """다른 트랜잭션이 잠금을 쥐고 있으면 끝없이 기다리지 않고 일시 오류가 된다."""
    with engine.connect() as holder:
        holder.execute(text("LOCK TABLE users IN ACCESS EXCLUSIVE MODE"))
        with timeout_engine.connect() as waiter, pytest.raises(sa_exc.OperationalError) as caught:
            waiter.execute(text("SELECT 1 FROM users"))
        holder.rollback()

    assert is_transient_db_error(caught.value)


def test_slow_statement_ends_as_transient_error(timeout_engine: Engine) -> None:
    with timeout_engine.connect() as conn, pytest.raises(sa_exc.OperationalError) as caught:
        conn.execute(text("SELECT pg_sleep(2)"))

    assert is_transient_db_error(caught.value)
