"""1차 배포 별점을 지워도 마이페이지 의견은 남고, 별점이 남아 있으면 지우지 않는다."""

import uuid
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine, text

from pitch_coach_backend.core.config import settings

# 이 PR 의 마이그레이션 바로 앞
BEFORE = "a74200000001"


@pytest.fixture
def migration(engine, monkeypatch):
    assert engine.url.database.endswith("_test")
    database = "feedback_drop_" + uuid.uuid4().hex
    admin = create_engine(engine.url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    migration_engine = create_engine(engine.url.set(database=database))
    config = Config()
    config.set_main_option("script_location", str(Path(__file__).resolve().parents[2] / "alembic"))
    monkeypatch.setattr(
        settings, "database_url", migration_engine.url.render_as_string(hide_password=False)
    )
    with admin.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{database}"'))
    try:
        command.upgrade(config, BEFORE)
        yield config, migration_engine
    finally:
        migration_engine.dispose()
        with admin.connect() as connection:
            connection.execute(text(f'DROP DATABASE IF EXISTS "{database}" WITH (FORCE)'))
        admin.dispose()


def _seed(migration_engine, *, rating: bool):
    values = {"user_id": uuid.uuid4(), "feedback_id": uuid.uuid4()}
    with migration_engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO users (id, email, name) VALUES (:user_id, 'drop@example.com', '삭제')"
            ),
            values,
        )
        connection.execute(
            text(
                "INSERT INTO user_suggestions (id, user_id, content, source, request_id) "
                "VALUES (gen_random_uuid(), :user_id, '마이페이지 의견', 'MY_PAGE', "
                "gen_random_uuid())"
            ),
            values,
        )
        connection.execute(
            text(
                "INSERT INTO service_feedback_impressions "
                "(id, user_id, display_request_id, display_number) "
                "VALUES (gen_random_uuid(), :user_id, gen_random_uuid(), 1)"
            ),
            values,
        )
        if rating:
            connection.execute(
                text(
                    "INSERT INTO service_feedbacks (id, user_id, rating) "
                    "VALUES (:feedback_id, :user_id, 4)"
                ),
                values,
            )


def test_drop_keeps_mypage_suggestions_and_round_trips(migration):
    config, migration_engine = migration
    _seed(migration_engine, rating=False)
    command.upgrade(config, "head")
    command.check(config)
    with migration_engine.connect() as connection:
        assert connection.scalar(text("SELECT content FROM user_suggestions")) == "마이페이지 의견"
        for table in ("service_feedbacks", "service_feedback_impressions"):
            assert connection.scalar(text(f"SELECT to_regclass('{table}')")) is None
    command.downgrade(config, BEFORE)
    command.upgrade(config, "head")
    command.check(config)


def test_drop_refuses_when_ratings_remain(migration):
    config, migration_engine = migration
    _seed(migration_engine, rating=True)
    with pytest.raises(RuntimeError, match="지우지 않습니다"):
        command.upgrade(config, "head")
    with migration_engine.connect() as connection:
        assert MigrationContext.configure(connection).get_current_revision() == BEFORE
        assert connection.scalar(text("SELECT count(*) FROM service_feedbacks")) == 1
        assert connection.scalar(text("SELECT count(*) FROM service_feedback_impressions")) == 1
