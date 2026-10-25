"""AI 서비스의 FastAPI 앱을 만든다.

- 부르는 곳: uvicorn(`uvicorn pitch_coach_ai.main:app`),
  테스트의 `client` fixture(`create_app()`)
- 하는 일: 설정을 읽고 앱을 만들어 예외 처리 · 요청 로그 · 라우터를 붙인다.
  시작할 때(lifespan) 서버 안 모델(대본 전달도의 형태소 분석기)을 별도 스레드에서 불러오고,
  끝나기 전까지 그 모델을 쓰는 경로와 `/ready` 는 503 `NOT_READY` 다 (`common/readiness.py`).
  로직은 두지 않는다
- 시작 오류: 이 모듈을 import 할 때 `settings` 를 읽는다. 모델 묶음이 일부만
  차 있으면 여기서 실패해 uvicorn 이 뜨지 않는다 (`settings.py`)
- 고칠 때: 새 경로는 `api/<이름>.py` 에 라우터로 만들고 `create_app` 에서
  `include_router` 한다. 시작할 때 불러올 모델이 있으면 그 모듈의 `WARMUP` 을 `_WARMUP` 에
  더한다. BE 와 달리 경로에 `/api` 접두어가 없다. AI 는
  내부망에서 BE 만 부르고 Caddy 를 거치지 않는다 (`docs/ai/architecture.md` 2절)
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from pitch_coach_ai.api import health, script_coverage
from pitch_coach_ai.common.errors import register_exception_handlers
from pitch_coach_ai.common.readiness import Readiness
from pitch_coach_ai.common.request_log import configure_logging, register_request_log
from pitch_coach_ai.settings import Settings, settings

#: 시작할 때 불러올 서버 안 모델 (기능 라우터 모듈의 WARMUP)
_WARMUP = [*script_coverage.WARMUP]


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    app.state.readiness.start()
    yield


def create_app(app_settings: Settings = settings) -> FastAPI:
    """라우터를 붙인 새 앱을 돌려준다.

    테스트가 설정을 바꿔 앱을 새로 만들 수 있게 함수로 두고 설정을 인자로 받는다.
    모듈 맨 아래 `app` 은 uvicorn 용이다.
    """
    configure_logging()
    app = FastAPI(title="Pitch Coach AI", version="0.1.0", lifespan=_lifespan)
    app.state.settings = app_settings
    app.state.readiness = Readiness(_WARMUP)
    # 모든 에러를 {"error": {"code", "message"}} 로, 요청마다 X-Request-ID 와 한 줄 로그
    register_exception_handlers(app)
    register_request_log(app)
    app.include_router(health.router)
    app.include_router(script_coverage.router)
    return app


app = create_app()
