from fastapi import APIRouter, FastAPI

from pitch_coach_backend import entities  # noqa: F401  # 모든 entity 를 매퍼에 등록
from pitch_coach_backend.core.exceptions import register_exception_handlers

# Caddy 는 /api/* 만 백엔드로 넘긴다 (infra/Caddyfile). 접두어 없는 경로는 404 도 아니고
# 프론트 화면이 돌아온다. 그래서 /docs·/openapi.json 까지 전부 /api 아래로 옮긴다.
# 버전 접두어(/v1)는 붙이지 않는다 — 설계 문서 경로에 /api 만 앞에 붙는 형태다.
API_PREFIX = "/api"

app = FastAPI(
    title="Pitch Coach API",
    version="0.1.0",
    docs_url=f"{API_PREFIX}/docs",
    redoc_url=f"{API_PREFIX}/redoc",
    openapi_url=f"{API_PREFIX}/openapi.json",
    swagger_ui_oauth2_redirect_url=f"{API_PREFIX}/docs/oauth2-redirect",
)
register_exception_handlers(app)

# 도메인 controller 는 모두 이 라우터에 붙인다. /api 는 이 파일 한 곳에만 적는다.
# controller 쪽 prefix 는 설계 문서 그대로 쓴다 (예: APIRouter(prefix="/users")).
api = APIRouter(prefix=API_PREFIX)


@api.get("/health", tags=["health"])
def health() -> dict[str, str]:
    return {"status": "ok"}


app.include_router(api)
