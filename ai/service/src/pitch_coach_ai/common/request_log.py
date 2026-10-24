"""요청 ID(`X-Request-ID`)와 요청마다 한 줄 로그.

- 부르는 곳: `main.py` (`configure_logging()` · `register_request_log(app)`),
  요청 ID 가 필요한 로그(예: `common/llm.py`)는 `current_request_id()`
- 하는 일 (요청 하나마다)
  1. 요청 헤더 `X-Request-ID` 를 받으면 그대로, 없으면 32자리 ID 를 새로 만든다
  2. 라우터를 부르고, 처리되지 않은 예외는 여기서 잡아 500 공통 에러 본문으로 바꾼다
     (바깥으로 내보내면 미들웨어를 지나친 500 응답에 요청 ID 가 빠진다)
  3. 응답 헤더에 같은 `X-Request-ID` 를 싣는다
  4. `request_id=… method=… path=… status=… code=… duration_ms=…` 한 줄을 남긴다
- 로그 규칙 (`docs/ai/architecture.md` 11절): 표준 출력으로만 낸다. 요청 본문 · 쿼리 문자열 ·
  키 · 예외 메시지는 남기지 않는다(예외는 종류와 위치만). 경로 · 요청 ID 는 밖에서 온 값이라
  공백 · 제어 문자를 이스케이프한다. `code` 는 에러 응답일 때 공통 에러 코드, 아니면 `-`
- 로거: `pitch_coach_ai` 와 그 아래(`pitch_coach_ai.request` · `.errors` · `.llm`)에만 출력기를
  단다. root 에 달면 LLM 라이브러리(httpx)의 INFO 로그까지 찍혀 게이트웨이 주소가 남는다
"""

import logging
import sys
import time
import uuid
from collections.abc import Awaitable, Callable
from contextvars import ContextVar

from fastapi import FastAPI, Request, Response

from pitch_coach_ai.common.errors import (
    exception_summary,
    internal_error_response,
    log_safe,
)

REQUEST_ID_HEADER = "X-Request-ID"

# 요청마다 따로인 변수. 미들웨어가 정하고, 같은 요청 안의 코드(def 라우터의 스레드 포함)가 읽는다
_request_id: ContextVar[str | None] = ContextVar("request_id", default=None)

logger = logging.getLogger("pitch_coach_ai.request")


def current_request_id() -> str | None:
    """지금 처리 중인 요청의 ID. 요청 밖(시작할 때 등)이면 None."""
    return _request_id.get()


def configure_logging() -> None:
    """`pitch_coach_ai` 로거가 INFO 이상을 표준 출력으로 내게 한다. 여러 번 불러도 한 번만 단다.

    uvicorn 의 접근 로그(`"GET /health?x=1 HTTP/1.1" 200`)는 끈다. 요청마다 아래 요청 로그와
    겹치고, 쿼리 문자열을 그대로 싣고, 요청 ID 가 없다. uvicorn 은 앱을 불러오기 전에 로그를
    설정하므로 여기서 끈 것이 유지된다(시작 · 오류 로그는 그대로 나온다).
    """
    logging.getLogger("uvicorn.access").disabled = True
    root = logging.getLogger("pitch_coach_ai")
    if root.handlers:
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    # root 로거로 올려 보내지 않는다 (uvicorn 등 다른 출력기와 두 번 찍히지 않게)
    root.propagate = False


def register_request_log(app: FastAPI) -> None:
    @app.middleware("http")
    async def request_log(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request_id = request.headers.get(REQUEST_ID_HEADER) or uuid.uuid4().hex
        token = _request_id.set(request_id)
        # 예외 처리기가 적는 에러 코드를 같은 요청 상태에서 읽는다 (errors.py)
        request.state.error_code = None
        started = time.perf_counter()
        try:
            try:
                response = await call_next(request)
            except Exception as exc:  # noqa: BLE001  # 모든 예외를 공통 500 으로
                logger.error(
                    "unhandled_error request_id=%s path=%s error=%s",
                    log_safe(request_id),
                    log_safe(request.url.path),
                    exception_summary(exc),
                )
                response = internal_error_response()
                request.state.error_code = "INTERNAL_ERROR"
            response.headers[REQUEST_ID_HEADER] = request_id
            logger.info(
                "request_id=%s method=%s path=%s status=%s code=%s duration_ms=%.1f",
                log_safe(request_id),
                request.method,
                log_safe(request.url.path),
                response.status_code,
                request.state.error_code or "-",
                (time.perf_counter() - started) * 1000,
            )
            return response
        finally:
            _request_id.reset(token)
