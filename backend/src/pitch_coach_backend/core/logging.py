"""앱 로그 설정.

uvicorn 기본 설정은 `uvicorn.*` 로거만 잡는다. 그대로 두면 앱 로거(`pitch_coach_backend.*`)는
핸들러가 없어서 INFO 는 버려지고, WARNING 이상은 파이썬 기본 처리로 메시지만(레벨 · 시각 · 이름
없이) 나간다. 그래서 앱 로거에만 핸들러를 단다.
Uvicorn의 예외 로그는 아래 필터로 본문·traceback을 제거한다. 앱 로그의 DB 예외는 본문만 뺀다.

루트가 아니라 앱 로거에 다는 이유: uvicorn 로거는 루트로 올라가지 않아 겹치지 않지만, 루트에 달면
httpx · websockets 같은 라이브러리의 INFO 까지 같이 나온다. 앱 로그는 그대로 루트로도 올라가서
pytest `caplog` 는 지금처럼 잡는다.
"""

import logging
import re
import traceback

import psycopg
from sqlalchemy import exc as sa_exc

APP_LOGGER = "pitch_coach_backend"
LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"
# 이미 단 핸들러를 알아보는 이름. 앱이 여러 번 떠도(테스트) 하나만 단다
_HANDLER_NAME = "pitch_coach_backend"


class _ServerExceptionFilter(logging.Filter):
    """서버가 다시 기록하는 예외도 원문 없이 진단한다. 일반 서버 로그는 그대로 둔다."""

    def filter(self, record: logging.LogRecord) -> bool:
        if record.exc_info:
            error = record.exc_info[1]
            record.msg = "ASGI 실행 실패 error=%s sqlstate=%s"
            record.args = (type(error).__name__, sqlstate(error))
            record.exc_info = None
            # 다른 핸들러가 먼저 포맷한 traceback도 재사용하지 않는다.
            record.exc_text = None
            record.stack_info = None
        return True


class _DbErrorFilter(logging.Filter):
    """앱 로그의 DB 예외는 본문 대신 예외 종류 · SQLSTATE 와 호출 위치만 남긴다.

    바인드 값은 엔진의 `hide_parameters` 가 가리지만 DB 오류 본문에도 사용자 값이 붙는다
    (제약 위반의 `Failing row contains (...)` 는 행 전체다). 다른 예외의 traceback 은 그대로 둔다.
    자식 로거의 기록에는 부모 로거의 필터가 걸리지 않아 핸들러에 단다.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        error = record.exc_info[1] if record.exc_info else None
        if error is None:
            return True
        chain = _exception_chain(error)
        if not any(isinstance(item, (sa_exc.SQLAlchemyError, psycopg.Error)) for item in chain):
            return True
        frames = "".join(traceback.format_list(traceback.extract_tb(error.__traceback__)))
        names = " <- ".join(type(item).__name__ for item in chain)
        code = next((code for item in chain if (code := sqlstate(item)) is not None), None)
        summary = f"{names} (DB 오류 본문 생략) sqlstate={code}"
        record.exc_text = f"Traceback (most recent call last):\n{frames}{summary}"
        record.exc_info = None
        record.stack_info = None
        return True


def _exception_chain(error: BaseException) -> list[BaseException]:
    """`error` 와 그 원인(`__cause__` · `__context__`)을 바깥부터."""
    chain: list[BaseException] = []
    current: BaseException | None = error
    while current is not None and current not in chain:
        chain.append(current)
        current = current.__cause__ or current.__context__
    return chain


def configure_logging(level: str) -> None:
    """앱 로거에 `level` 과 출력 형식을 건다. 여러 번 불러도 핸들러는 하나다.

    모르는 레벨이면 `ValueError` — 앱이 시작할 때 실패한다.
    """
    logger = logging.getLogger(APP_LOGGER)
    logger.setLevel(level.upper())
    # Starlette는 500 응답 뒤에도 원래 예외를 올려 Uvicorn이 다시 기록한다.
    server_logger = logging.getLogger("uvicorn.error")
    if not any(isinstance(item, _ServerExceptionFilter) for item in server_logger.filters):
        server_logger.addFilter(_ServerExceptionFilter())
    if any(handler.get_name() == _HANDLER_NAME for handler in logger.handlers):
        return
    handler = logging.StreamHandler()
    handler.set_name(_HANDLER_NAME)
    handler.addFilter(_DbErrorFilter())
    handler.setFormatter(logging.Formatter(LOG_FORMAT))
    logger.addHandler(handler)


def sqlstate(exc: BaseException) -> str | None:
    """DB 드라이버가 준 SQLSTATE (예: `08006`). 다섯 자리 코드라 사용자 원문이 섞이지 않는다.

    SQLAlchemy 예외 문자열에는 SQL 매개변수와 DB 오류 메시지가 붙는다. 저장 실패 로그에는 예외
    본문 대신 예외 종류와 이 값을 남긴다. SQLAlchemy 는 드라이버 예외를 `orig` 에 담는다. 그 밖의
    예외나 모양이 다르면 None.
    """
    code = getattr(getattr(exc, "orig", None), "sqlstate", None)
    return code if isinstance(code, str) and re.fullmatch(r"[0-9A-Z]{5}", code) else None
