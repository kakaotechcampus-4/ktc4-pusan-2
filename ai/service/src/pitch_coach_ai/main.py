"""AI 서비스의 FastAPI 앱을 만든다.

- 부르는 곳: uvicorn(`uvicorn pitch_coach_ai.main:app`), 테스트의 `client` fixture(`create_app()`)
- 하는 일: 앱을 만들고 라우터를 붙인다. 로직은 두지 않는다
- 고칠 때: 새 경로는 `api/<이름>.py` 에 라우터로 만들고 `create_app` 에서 `include_router` 한다.
  BE 와 달리 경로에 `/api` 접두어가 없다. AI 는 내부망에서 BE 만 부르고 Caddy 를 거치지 않는다
  (`docs/ai/architecture.md` 2절)
"""

from fastapi import FastAPI

from pitch_coach_ai.api import health


def create_app() -> FastAPI:
    """라우터를 붙인 새 앱을 돌려준다.

    테스트가 설정을 바꿔 앱을 새로 만들 수 있게 함수로 둔다. 모듈 맨 아래 `app` 은 uvicorn 용이다.
    """
    app = FastAPI(title="Pitch Coach AI", version="0.1.0")
    app.include_router(health.router)
    return app


app = create_app()
