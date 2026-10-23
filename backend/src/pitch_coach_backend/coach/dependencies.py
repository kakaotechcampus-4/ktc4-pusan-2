"""코치 클라이언트. 테스트는 `app.dependency_overrides` 로 가짜를 끼운다.

클라이언트는 프로세스에 하나다. 백그라운드 호출(plan · finalize)의 동시 개수 제한과 AI 가 없을 때
evaluate 를 쉬는 시각을 이 객체가 들고 있어서, 부르는 곳마다 만들면 제한이 그만큼 늘어난다.
"""

from typing import Annotated

from fastapi import Depends

from pitch_coach_backend.coach.client import CoachClient, HttpCoachClient
from pitch_coach_backend.core.config import settings

_coach_client = HttpCoachClient(settings.ai_base_url)


def get_coach_client() -> CoachClient:
    return _coach_client


CoachClientDep = Annotated[CoachClient, Depends(get_coach_client)]
