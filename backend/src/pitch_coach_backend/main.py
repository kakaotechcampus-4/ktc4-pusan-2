from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from pitch_coach_backend import entities  # noqa: F401  # 모든 entity 를 매퍼에 등록
from pitch_coach_backend.coach.background import start as start_coach_background
from pitch_coach_backend.coach.dependencies import get_coach_client, get_coach_store
from pitch_coach_backend.core.config import settings
from pitch_coach_backend.core.exceptions import register_exception_handlers
from pitch_coach_backend.core.logging import configure_logging
from pitch_coach_backend.core.s3 import get_s3
from pitch_coach_backend.module import hooks
from pitch_coach_backend.module.admin.controller import router as admin_router
from pitch_coach_backend.module.auth.controller import router as auth_router
from pitch_coach_backend.module.auth.dependencies import CSRF_HEADER_NAME
from pitch_coach_backend.module.pitch.controller import router as pitch_router
from pitch_coach_backend.module.take.controller import router as take_router
from pitch_coach_backend.module.take.controller import takes_router
from pitch_coach_backend.module.take.ready_sweep import start as start_ready_sweep
from pitch_coach_backend.module.user.controller import router as user_router
from pitch_coach_backend.realtime.auto_end import AutoEndPolicy
from pitch_coach_backend.realtime.controller import router as realtime_router
from pitch_coach_backend.realtime.dependencies import get_transcript_store
from pitch_coach_backend.realtime.live_takes import RealtimeLiveTakes
from pitch_coach_backend.realtime.take_stream import arm_abandoned_takes
from pitch_coach_backend.realtime.take_stream import stop_all as stop_all_streams

# Caddy 는 /api/* 만 백엔드로 넘긴다 (infra/Caddyfile). 접두어 없는 경로는 404 도 아니고
# 프론트 화면이 돌아온다. 그래서 /docs·/openapi.json 까지 전부 /api 아래로 옮긴다.
# 버전 접두어(/v1)는 붙이지 않는다 — 설계 문서 경로에 /api 만 앞에 붙는 형태다.
# 예외는 관리자 콘솔(/admin) 하나다. 앱 API 가 아니라서 Caddy 가 따로 넘긴다.
API_PREFIX = "/api"

hooks.install(live_takes=RealtimeLiveTakes(), coaching=hooks.coaching())


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # 아래에서 남기는 로그부터 나오도록 맨 먼저 건다. 레벨이 틀렸으면 지금 실패한다
    configure_logging(settings.log_level)
    get_s3()
    # 자동 종료 설정이 틀렸으면 첫 연습이 아니라 지금 실패한다
    AutoEndPolicy.from_settings()
    # 떠난 Take 를 끝내는 타이머는 메모리에 있어 다시 뜨면 사라진다. 진행 중인 Take 에 다시 건다
    await arm_abandoned_takes(get_transcript_store())
    stop_coach_background = await start_coach_background(get_coach_client(), get_coach_store())
    # 시작하지 않은 채 남은 Take 를 끝내는 주기 작업. 뜰 때 한 번 하고 1분마다 한다
    stop_ready_sweep = start_ready_sweep()
    yield
    # 실시간 STT 스트림은 WebSocket 요청 밖에서 도는 백그라운드 태스크다.
    # 종료 시 Deepgram 에 CloseStream 을 보내 마지막 전사를 받고 정리한다.
    await stop_all_streams()
    await stop_ready_sweep()
    await stop_coach_background()


app = FastAPI(
    title="Pitch Coach API",
    version="0.1.0",
    docs_url=f"{API_PREFIX}/docs",
    redoc_url=f"{API_PREFIX}/redoc",
    openapi_url=f"{API_PREFIX}/openapi.json",
    swagger_ui_oauth2_redirect_url=f"{API_PREFIX}/docs/oauth2-redirect",
    lifespan=lifespan,
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
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
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
api.include_router(pitch_router)
api.include_router(take_router)
api.include_router(takes_router)
api.include_router(realtime_router)

app.include_router(api)
app.include_router(admin_router)
