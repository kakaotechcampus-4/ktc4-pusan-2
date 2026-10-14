"""AI 서비스의 FastAPI 앱을 만든다.

- 부르는 곳: uvicorn(`uvicorn pitch_coach_ai.main:app`),
  테스트의 `client` fixture(`create_app()`)
- 하는 일: 설정을 읽고 앱을 만들어 예외 처리 · 요청 로그 · 라우터를 붙인다.
  로직은 두지 않는다
- 시작 오류: 이 모듈을 import 할 때 `settings` 를 읽는다. 모델 묶음이 일부만
  차 있으면 여기서 실패해 uvicorn 이 뜨지 않는다 (`settings.py`)
- 고칠 때: 새 경로는 `api/<이름>.py` 에 라우터로 만들고 `create_app` 에서
  `include_router` 한다. BE 와 달리 경로에 `/api` 접두어가 없다. AI 는
  내부망에서 BE 만 부르고 Caddy 를 거치지 않는다 (`docs/ai/architecture.md` 2절)
"""

from fastapi import FastAPI

from pitch_coach_ai.api import health
from pitch_coach_ai.common.errors import register_exception_handlers
from pitch_coach_ai.common.request_log import configure_logging, register_request_log
from pitch_coach_ai.settings import Settings, settings


def create_app(app_settings: Settings = settings) -> FastAPI:
    """라우터를 붙인 새 앱을 돌려준다.

    테스트가 설정을 바꿔 앱을 새로 만들 수 있게 함수로 두고 설정을 인자로 받는다.
    모듈 맨 아래 `app` 은 uvicorn 용이다.
    """
    configure_logging()
    app = FastAPI(title="Pitch Coach AI", version="0.1.0")
    app.state.settings = app_settings
    # 모든 에러를 {"error": {"code", "message"}} 로, 요청마다 X-Request-ID 와 한 줄 로그
    register_exception_handlers(app)
    register_request_log(app)
    app.include_router(health.router)
    return app


app = create_app()
