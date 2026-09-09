from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from pitch_coach_backend import entities  # noqa: F401  # 모든 entity 를 매퍼에 등록
from pitch_coach_backend.core.config import settings
from pitch_coach_backend.core.exceptions import register_exception_handlers
from pitch_coach_backend.module.auth.controller import router as auth_router
from pitch_coach_backend.module.auth.dependencies import CSRF_HEADER_NAME
from pitch_coach_backend.module.user.controller import router as user_router

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

# 로컬은 localhost:3000(프론트) 과 localhost:8000(백엔드) 이 cross-origin 이라
# 쿠키를 주고받으려면 이 설정이 필요하다. 운영은 Caddy 가 같은 origin 으로 묶어
# CORS 자체가 발생하지 않는다.
#
# allow_credentials=True 는 allow_origins=["*"] 와 함께 쓸 수 없다 (브라우저가 거부한다).
# 그래서 프론트 주소를 명시한다.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_base_url.rstrip("/")],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", CSRF_HEADER_NAME],
)

# 도메인 controller 는 모두 이 라우터에 붙인다. /api 는 이 파일 한 곳에만 적는다.
# controller 쪽 prefix 는 설계 문서 그대로 쓴다 (예: APIRouter(prefix="/users")).
api = APIRouter(prefix=API_PREFIX)


@api.get("/health", tags=["health"])
def health() -> dict[str, str]:
    return {"status": "ok"}


api.include_router(auth_router)
api.include_router(user_router)

app.include_router(api)
