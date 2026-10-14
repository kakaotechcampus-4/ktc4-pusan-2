"""공통 fixture.

- 모델 묶음 환경변수를 빈 값으로 덮은 뒤에 앱을 import 한다. 진짜 환경변수가
  `ai/.env` 보다 앞서므로, 단위 테스트는 로컬 `ai/.env` 의 실제 키를 읽지 않는다.
  그래서 내 PC 와 CI(키 없음)의 결과가 같고, 실수로 실제 LLM 을 부를 수 없다.
  실제 호출은 `tests/live/` 가 `ai/.env` 를 직접 읽어 따로 한다
- `client`: 실제 네트워크 없이 앱에 요청을 보내는 TestClient. 테스트마다 앱을
  새로 만들어 한 테스트의 설정이 다른 테스트로 새지 않게 한다
- `probe_client`: `client` 와 같은 앱에 실패를 일부러 내는 테스트 전용 경로(`/_probe/...`)를
  붙인 것. 에러 본문 · 요청 로그 테스트가 쓴다
- `log_records`: `pitch_coach_ai` 로거가 남긴 로그를 모은 목록. 이 로거는 root 로 올려
  보내지 않아(request_log.py) pytest 의 caplog 로는 잡히지 않는다
"""

import os

for _name in (
    "OPENAI_BASE_URL",
    "OPENAI_API_KEY",
    "OPENAI_MODEL",
    "OPENAI_SOL_BASE_URL",
    "OPENAI_SOL_API_KEY",
    "OPENAI_SOL_MODEL",
):
    os.environ[_name] = ""
# 실행하는 셸에 값이 있어도 기본값으로 테스트한다 (진짜 환경변수가 .env 보다 앞선다)
os.environ["LLM_TIMEOUT_SECONDS"] = "12"
os.environ["LLM_MAX_RETRIES"] = "1"
os.environ["WEB_CONCURRENCY"] = "1"

import logging  # noqa: E402  # 위 환경변수를 먼저 넣어야 한다
from collections.abc import Iterator  # noqa: E402

import pytest  # noqa: E402
from fastapi import APIRouter  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from pydantic import BaseModel  # noqa: E402

from pitch_coach_ai.common import errors  # noqa: E402
from pitch_coach_ai.main import create_app  # noqa: E402


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(create_app()) as c:
        yield c


class _ProbeBody(BaseModel):
    text: str
    count: int


class _ProbeScores(BaseModel):
    scores: dict[str, int]


_LLM_ERRORS: dict[str, type[errors.LlmError]] = {
    "timeout": errors.LlmTimeout,
    "error": errors.LlmCallFailed,
    "invalid": errors.LlmInvalidOutput,
    "unavailable": errors.LlmUnavailable,
}


def _probe_router() -> APIRouter:
    router = APIRouter(prefix="/_probe")

    @router.post("/echo")
    def echo(body: _ProbeBody) -> dict[str, int]:
        return {"length": len(body.text)}

    @router.post("/scores")
    def scores(body: _ProbeScores) -> dict[str, int]:
        return {"count": len(body.scores)}

    @router.get("/llm/{kind}")
    def llm_error(kind: str) -> None:
        raise _LLM_ERRORS[kind]()

    @router.get("/boom")
    def boom() -> None:
        raise RuntimeError("대본 원문이 들어간 내부 예외")

    return router


@pytest.fixture
def probe_client() -> Iterator[TestClient]:
    app = create_app()
    app.include_router(_probe_router())
    with TestClient(app) as c:
        yield c


class _ListHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


@pytest.fixture
def log_records() -> Iterator[list[logging.LogRecord]]:
    handler = _ListHandler()
    logger = logging.getLogger("pitch_coach_ai")
    logger.addHandler(handler)
    try:
        yield handler.records
    finally:
        logger.removeHandler(handler)
