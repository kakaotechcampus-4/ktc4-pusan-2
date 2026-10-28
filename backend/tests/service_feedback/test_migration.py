"""이미 저장된 평점·의견도 추가 마이그레이션 뒤 탈퇴 시 유지한다."""

import uuid
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, text

from pitch_coach_backend.core.config import settings


@pytest.mark.parametrize("withdraw", [False, True])
def test_retention_upgrade_preserves_existing_rows_and_safe_downgrade(
    engine, monkeypatch, withdraw
):
    assert engine.url.database.endswith("_test")
    database = "feedback_retention_" + uuid.uuid4().hex
    admin = create_engine(engine.url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    migration_engine = create_engine(engine.url.set(database=database))
    config = Config()
    config.set_main_option("script_location", str(Path(__file__).resolve().parents[2] / "alembic"))
    monkeypatch.setattr(
        settings, "database_url", migration_engine.url.render_as_string(hide_password=False)
    )
    user_id, feedback_id, popup_id, mypage_id, display_id = [uuid.uuid4() for _ in range(5)]
    values = dict(
        user_id=user_id,
        feedback_id=feedback_id,
        popup_id=popup_id,
        mypage_id=mypage_id,
        display_id=display_id,
        request_id=uuid.uuid4(),
    )
    try:
        with admin.connect() as connection:
            connection.execute(text(f'CREATE DATABASE "{database}"'))
        command.upgrade(config, "f54000000001")
        with migration_engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO users (id, email, name) VALUES (:user_id, "
                    "'retention@example.com', '보관 검증')"
                ),
                values,
            )
            connection.execute(
                text(
                    "INSERT INTO service_feedbacks (id, user_id, rating) VALUES (:feedback_id, "
                    ":user_id, 4)"
                ),
                values,
            )
            connection.execute(
                text(
                    "INSERT INTO user_suggestions (id, user_id, content, source, "
                    "service_feedback_id) VALUES "
                    "(:popup_id, :user_id, '팝업 의견', 'RATING_POPUP', "
                    ":feedback_id)"
                ),
                values,
            )
            connection.execute(
                text(
                    "INSERT INTO user_suggestions (id, user_id, content, source, request_id) "
                    "VALUES (:mypage_id, :user_id, '마이페이지 의견', 'MY_PAGE', :request_id)"
                ),
                values,
            )
            connection.execute(
                text(
                    "INSERT INTO service_feedback_impressions (id, user_id, display_request_id, "
                    "display_number) VALUES (:display_id, :user_id, :request_id, 1)"
                ),
                values,
            )
        command.upgrade(config, "head")
        command.check(config)
        with migration_engine.connect() as connection:
            assert connection.scalars(
                text("SELECT DISTINCT category FROM user_suggestions")
            ).all() == ["OTHER"]
        if withdraw:
            with migration_engine.begin() as connection:
                connection.execute(text("DELETE FROM users WHERE id = :user_id"), values)
                assert (
                    connection.scalar(
                        text(
                            "SELECT rating FROM service_feedbacks WHERE id = :feedback_id AND "
                            "user_id IS NULL"
                        ),
                        values,
                    )
                    == 4
                )
                assert (
                    connection.scalar(
                        text("SELECT count(*) FROM user_suggestions WHERE user_id IS NULL")
                    )
                    == 2
                )
                assert (
                    connection.scalar(
                        text(
                            "SELECT service_feedback_id FROM user_suggestions WHERE id = :popup_id"
                        ),
                        values,
                    )
                    == feedback_id
                )
                assert (
                    connection.scalar(text("SELECT count(*) FROM service_feedback_impressions"))
                    == 0
                )
            with pytest.raises(RuntimeError, match="이전 스키마로 되돌릴 수 없습니다"):
                command.downgrade(config, "f54000000001")
            with migration_engine.connect() as connection:
                assert (
                    MigrationContext.configure(connection).get_current_revision()
                    == ScriptDirectory.from_config(config).get_current_head()
                )
                assert connection.scalar(text("SELECT count(*) FROM service_feedbacks")) == 1
                assert connection.scalar(text("SELECT count(*) FROM user_suggestions")) == 2
        else:
            command.downgrade(config, "f54000000001")
            command.upgrade(config, "head")
            command.check(config)
    finally:
        migration_engine.dispose()
        with admin.connect() as connection:
            connection.execute(text(f'DROP DATABASE IF EXISTS "{database}" WITH (FORCE)'))
        admin.dispose()
