"""`GET /health` · `GET /ready` — 프로세스가 살아 있는지, 기능 요청을 받을 준비가 됐는지 알린다.

- `/health`: 프로세스가 살아 있으면 200 `{"status": "ok"}`. 배포 확인 명령이 본다
- `/ready`: 시작할 때 불러오는 서버 안 모델(대본 전달도의 형태소 분석기)까지 준비됐으면 200
  `{"status": "ready"}`, 아직이면 503 `NOT_READY`(공통 에러 본문). compose 헬스체크가 본다
  (`docs/ai/ai-service.md` 6-3 · 11-3)
- 둘 다 `async def` 다. 긴 LLM 요청이 동기 라우터의 스레드 풀을 채워도 기다리지 않는다
- 둘 다 LLM 을 부르지 않는다. 부를 때마다 비용 · 지연이 생기고, 키가 없을 때도 200 이어야 한다
  (`docs/ai/ai-service.md` 4-2). 모델이 실제로 응답하는지는 live 테스트로 따로 본다
"""

from fastapi import APIRouter, Depends

from pitch_coach_ai.common.errors import ERROR_RESPONSES
from pitch_coach_ai.common.readiness import require_ready

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/ready", dependencies=[Depends(require_ready)], responses={503: ERROR_RESPONSES[503]})
async def ready() -> dict[str, str]:
    return {"status": "ready"}
