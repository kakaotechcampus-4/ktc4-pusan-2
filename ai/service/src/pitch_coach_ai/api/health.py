"""`GET /health` — 프로세스가 살아 있는지 알린다.

- 부르는 곳: compose 헬스체크(10초마다), 배포 확인 명령
- 출력: 200 `{"status": "ok"}`
- LLM 을 부르지 않는다. 부를 때마다 비용 · 지연이 생기고, 키가 없을 때도 200 이어야 한다
  (`docs/ai/ai-service.md` 4-2). 모델이 실제로 응답하는지는 live 테스트로 따로 본다
"""

from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
