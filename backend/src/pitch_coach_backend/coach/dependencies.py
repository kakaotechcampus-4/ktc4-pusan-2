"""코치 클라이언트 · 저장소. 요청 밖(백그라운드 작업 · `module/hooks.py` 의 구현)에서도
`get_coach_client()` · `get_coach_store()` 로 꺼내 쓴다. 그래서 테스트는 Depends 가 아니라 이 두
객체(`_coach_client` · `_coach_store`)를 가짜로 바꿔 끼운다.

클라이언트는 프로세스에 하나다. 백그라운드 호출(plan · finalize)의 동시 개수 제한과 AI 가 없을 때
evaluate 를 쉬는 시각을 이 객체가 들고 있어서, 부르는 곳마다 만들면 제한이 그만큼 늘어난다.
저장소는 부를 때마다 짧은 DB 세션을 열어 상태가 없다.
"""

from typing import Annotated

from fastapi import Depends

from pitch_coach_backend.coach.client import CoachClient, HttpCoachClient
from pitch_coach_backend.coach.store import CoachStore, DbCoachStore
from pitch_coach_backend.core.config import settings

_coach_client = HttpCoachClient(settings.ai_base_url)
_coach_store = DbCoachStore()


def get_coach_client() -> CoachClient:
    return _coach_client


def get_coach_store() -> CoachStore:
    return _coach_store


CoachClientDep = Annotated[CoachClient, Depends(get_coach_client)]
CoachStoreDep = Annotated[CoachStore, Depends(get_coach_store)]
