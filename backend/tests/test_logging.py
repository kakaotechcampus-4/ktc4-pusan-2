"""앱 로그 설정 (`core/logging.py`)."""

import io
import logging
import re
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError, OperationalError, StatementError

from pitch_coach_backend.core.database import engine
from pitch_coach_backend.core.logging import APP_LOGGER, configure_logging, sqlstate


@pytest.fixture(autouse=True)
def app_logger() -> Iterator[logging.Logger]:
    """앱 로거는 프로세스 전체에서 하나다. 테스트마다 비우고 끝나면 되돌린다."""
    logger = logging.getLogger(APP_LOGGER)
    handlers, level = list(logger.handlers), logger.level
    logger.handlers.clear()
    yield logger
    logger.handlers[:] = handlers
    logger.setLevel(level)


def captured(logger: logging.Logger) -> io.StringIO:
    """설정이 단 핸들러의 출력을 가로챈다."""
    [handler] = logger.handlers
    stream = io.StringIO()
    handler.setStream(stream)
    return stream


def test_app_info_logs_come_out_with_time_level_and_name(app_logger: logging.Logger):
    """uvicorn 기본 설정만으로는 INFO 가 버려지고 WARNING 은 메시지만 나온다."""
    configure_logging("info")
    stream = captured(app_logger)

    stream_logger = logging.getLogger(f"{APP_LOGGER}.realtime.stream.runtime")
    stream_logger.info("재연결 없이 grace 만료 take=%s", 1)
    stream_logger.debug("나오지 않는다")

    pattern = (
        r"\d{4}-\d\d-\d\d \d\d:\d\d:\d\d,\d{3} INFO "
        r"pitch_coach_backend\.realtime\.stream\.runtime 재연결 없이 grace 만료 take=1\n"
    )
    assert re.fullmatch(pattern, stream.getvalue())


def test_configuring_again_keeps_one_handler_and_takes_the_new_level(app_logger: logging.Logger):
    """테스트처럼 앱이 여러 번 떠도 같은 로그가 두 번 나오지 않는다."""
    configure_logging("INFO")
    configure_logging("WARNING")
    stream = captured(app_logger)

    app_logger.info("나오지 않는다")
    app_logger.warning("나온다")

    assert stream.getvalue().count("\n") == 1
    assert "WARNING pitch_coach_backend 나온다" in stream.getvalue()


def test_unknown_level_fails_at_startup():
    with pytest.raises(ValueError, match="LOUD"):
        configure_logging("LOUD")


def test_other_loggers_are_left_alone():
    """루트에 달면 httpx · websockets 의 INFO 까지 나온다. 앱 로거에만 단다."""
    root = logging.getLogger()
    before = list(root.handlers), root.level

    configure_logging("INFO")

    assert (list(root.handlers), root.level) == before
    assert logging.getLogger("httpx").handlers == []


def test_app_logs_still_reach_caplog(caplog: pytest.LogCaptureFixture):
    """핸들러를 달아도 루트로 올라가서 다른 테스트의 caplog 는 그대로 잡는다."""
    configure_logging("INFO")

    with caplog.at_level("INFO"):
        logging.getLogger(f"{APP_LOGGER}.module").info("잡힌다")

    assert "잡힌다" in caplog.text


def test_app_start_configures_logging(client: TestClient, app_logger: logging.Logger):
    assert len(app_logger.handlers) == 1
    assert app_logger.level == logging.INFO


class _DriverError(Exception):
    def __init__(self, code: object) -> None:
        self.sqlstate = code


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (OperationalError("SELECT 1", {}, _DriverError("08006")), "08006"),
        (OperationalError("SELECT 1", {}, _DriverError("원문이 섞인 값")), None),
        (OperationalError("SELECT 1", {}, _DriverError(None)), None),
        (RuntimeError("db down"), None),
    ],
)
def test_sqlstate_reads_only_the_five_char_driver_code(error, expected):
    assert sqlstate(error) == expected


class _Violation(Exception):
    """제약 위반처럼 본문에 행 값이 붙은 드라이버 예외."""

    sqlstate = "23514"


def _raise_db_error() -> None:
    try:
        raise _Violation('Failing row contains (secret@example.com, "대본 원문")')
    except _Violation as e:
        raise IntegrityError("INSERT INTO users ...", {"email": "x"}, e) from e


def test_db_error_logs_keep_type_sqlstate_and_frames_but_not_the_row(app_logger: logging.Logger):
    """DB 오류 본문(`Failing row contains`)에 사용자 값이 붙는다. 자식 로거로 남겨도 뺀다."""
    configure_logging("INFO")
    stream = captured(app_logger)

    try:
        _raise_db_error()
    except IntegrityError:
        logging.getLogger(f"{APP_LOGGER}.module.take").exception("Take 저장 실패 take=%s", 7)

    output = stream.getvalue()
    assert "Take 저장 실패 take=7" in output
    assert "secret@example.com" not in output
    assert "대본 원문" not in output
    assert "IntegrityError <- _Violation (DB 오류 본문 생략) sqlstate=23514" in output
    assert "_raise_db_error" in output


def test_other_error_logs_keep_the_traceback(app_logger: logging.Logger):
    configure_logging("INFO")
    stream = captured(app_logger)

    try:
        raise RuntimeError("AI 응답이 이상하다")
    except RuntimeError:
        logging.getLogger(f"{APP_LOGGER}.module").exception("실패")

    assert "RuntimeError: AI 응답이 이상하다" in stream.getvalue()


def test_engine_hides_bind_parameters():
    """예외 문자열에 바인드 값을 붙이지 않는다."""
    params = {"email": "secret@example.com"}
    error = StatementError("오류", "SELECT :email", params, None, engine.hide_parameters)

    assert "secret@example.com" not in str(error)
