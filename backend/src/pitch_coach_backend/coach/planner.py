"""다음 Take 의 코칭 계획을 요청해 쌓아 둔다 (`/coach/plan`, 백그라운드).

부르는 때 (`docs/ai/architecture.md` 1-2, `docs/ai/features/coach-agent.md` 5-1)
  - 리뷰를 저장한 직후. 리뷰가 끝내 실패해도 부른다 (`previous_review` null)
  - 대본 파싱이 끝난 직후 (첫 Take 의 계획), 제한 시간 · 허용 범위를 바꾼 뒤

응답은 기본 계획(`fallback_reason`)도 한 행으로 쌓는다. 넣을 때 최신인지 보지 않는다 — 다음 Take 를
시작할 때 바탕 값이 그때 것과 같은 행 중 요청 시각이 가장 늦은 것을 고른다 (12-2). 그래서 늦게 온
옛 응답이 새 계획을 대신하지 못하고, 이 작업끼리 줄을 세울 필요도 없다.

AI 를 기다리는 동안 DB 세션을 쥐지 않는다. 실패는 던지지 않는다 — 계획이 없어도 코칭은 돈다.
"""

import asyncio
import logging
import uuid
from datetime import UTC, datetime

import anyio

from pitch_coach_backend.coach.client import CoachClient
from pitch_coach_backend.coach.dependencies import get_coach_client, get_coach_store
from pitch_coach_backend.coach.store import CoachStore
from pitch_coach_backend.core.tasks import TaskRegistry

logger = logging.getLogger(__name__)

# 돌고 있는 계획 요청
_running = TaskRegistry()


def spawn(pitch_id: uuid.UUID, *, client: CoachClient, store: CoachStore) -> None:
    """코칭 계획 요청을 백그라운드로 띄운다. 이벤트 루프 안에서 부른다."""
    _running.spawn(run(pitch_id, client=client, store=store), name=f"coach-plan:{pitch_id}")


async def stop_all(timeout: float) -> None:
    """앱 종료. 돌고 있는 계획 요청을 끊는다 — 계획이 없어도 코칭은 돈다."""
    await _running.stop_all(timeout)


async def run(pitch_id: uuid.UUID, *, client: CoachClient, store: CoachStore) -> None:
    try:
        request = await store.build_plan_request(pitch_id)
        if request is None:
            # 발표가 없거나 파싱이 끝난 대본이 없다. 입력 상한을 넘었어도 None 이다 (requests.py)
            return
        requested_at = datetime.now(UTC)
        reply = await client.plan(request)
        if reply is None:
            return
        if not await store.save_plan(pitch_id, request, reply, requested_at):
            logger.info("발표가 지워져 코칭 계획을 버렸다 pitch=%s", pitch_id)
    except Exception:
        logger.exception("코칭 계획을 요청 · 저장하지 못했다 pitch=%s", pitch_id)


class PlannerCoaching:
    """다음 Take 의 코칭 계획을 요청한다 (`module.hooks.Coaching` 구현). main.py 가 꽂는다.

    요청 스레드(리뷰 · 제한 시간 바꾸기)에서도 이벤트 루프(대본 파싱 뒤)에서도 불린다. 태스크는
    루프에서만 띄울 수 있어 요청 스레드면 루프로 넘긴다."""

    def plan_next_take(self, pitch_id: uuid.UUID) -> None:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            anyio.from_thread.run_sync(_spawn_with_deps, pitch_id)
            return
        _spawn_with_deps(pitch_id)


def _spawn_with_deps(pitch_id: uuid.UUID) -> None:
    spawn(pitch_id, client=get_coach_client(), store=get_coach_store())
