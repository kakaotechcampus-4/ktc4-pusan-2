import asyncio
import contextlib
import logging
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from functools import partial
from typing import Any

from sqlalchemy.orm import Session

from pitch_coach_backend.core import ai_slots
from pitch_coach_backend.core.database import SessionLocal, in_session
from pitch_coach_backend.core.tasks import TaskRegistry
from pitch_coach_backend.module.chat.ai_client import TurnFailure, TurnFinal, request_turn
from pitch_coach_backend.module.chat.ai_response import UPSTREAM_ERROR, ReassessArgs, StreamLine
from pitch_coach_backend.module.chat.dto import (
    NO_BUDGET_USED,
    ActionResultDTO,
    AgentResumeDTO,
    BudgetUsedDTO,
    SignedStateDTO,
)
from pitch_coach_backend.module.chat.service import action as action_service
from pitch_coach_backend.module.chat.service import reassess as reassess_service
from pitch_coach_backend.module.chat.service import request as request_service
from pitch_coach_backend.module.chat.service import run as run_service
from pitch_coach_backend.module.chat.service import turn as turn_service
from pitch_coach_backend.module.chat.service.run import ClaimedRun, RunLost

logger = logging.getLogger(__name__)

MAX_TURN_CALLS = 6
STOP_TIMEOUT_SEC = 5.0
LEASE_RENEW_SEC = 10.0

# Callable[매개변수, 반환값] : 해당 함수 자체를 매개변수와 반환값으로 지정할 수 있다.
type SessionFactory = Callable[[], Session]
type ResumeWith = Callable[[Session, uuid.UUID], turn_service.ResumePoint | None]

_running = TaskRegistry()


def start(
    run_id: uuid.UUID, session_factory: SessionFactory = SessionLocal
) -> asyncio.Task[None] | None:
    return _spawn(run_id, session_factory, None)


def resume(
    run_id: uuid.UUID, session_factory: SessionFactory = SessionLocal
) -> asyncio.Task[None] | None:
    return _spawn(run_id, session_factory, turn_service.resume_point)


def recover(
    run_id: uuid.UUID, session_factory: SessionFactory = SessionLocal
) -> asyncio.Task[None] | None:
    return _spawn(run_id, session_factory, turn_service.recovery_point)


def _spawn(
    run_id: uuid.UUID, session_factory: SessionFactory, resume_with: ResumeWith | None
) -> asyncio.Task[None] | None:
    if _running.running(run_id):
        return None
    return _running.spawn(
        run(run_id, session_factory, resume_with=resume_with),
        name=f"chat-turn:{run_id}",
        key=run_id,
    )


async def run(
    run_id: uuid.UUID,
    session_factory: SessionFactory = SessionLocal,
    *,
    resume_with: ResumeWith | None = None,
) -> None:
    turn = _Turn(session_factory)
    try:
        await turn.run(run_id, resume_with=resume_with)
    except asyncio.CancelledError:
        reason = "사용자 취소" if _running.cancel_requested(run_id) else "앱 종료"
        logger.info("대화 턴을 멈춘다 (%s) run=%s", reason, run_id)
        raise
    except Exception:
        logger.exception("대화 턴 실패 run=%s", run_id)
        await turn.give_up(TurnFailure(UPSTREAM_ERROR))


def cancel(run_id: uuid.UUID) -> bool:
    """취소가 커밋된 실행의 턴을 끊어 열린 AI 연결과 대화 자리를 돌려준다. 돌고 있지 않으면 False.

    실행 상태는 취소한 쪽이 이미 저장했다. 스레드풀에서 돌던 저장은 끝까지 가지만 판 번호가 달라
    반영되지 않는다. 이벤트 루프에서 부른다.
    """
    return _running.cancel(run_id)


async def stop_all(timeout: float = STOP_TIMEOUT_SEC) -> None:
    await _running.stop_all(timeout)


class _Turn:
    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory
        self._run: ClaimedRun | None = None

    # 세션을 열고, 실행을 claim 한 뒤, 턴을 반복한다. 턴이 끝나면 종료 상태를 저장한다.
    async def run(self, run_id: uuid.UUID, *, resume_with: ResumeWith | None) -> None:
        point = None
        if resume_with is not None:
            point = await in_session(self._session_factory, resume_with, run_id)
            self._run = point.run if point else None
        else:
            self._run = await in_session(self._session_factory, run_service.claim, run_id)
        if self._run is None:
            return
        remaining = (self._run.deadline - datetime.now(UTC)).total_seconds()
        heartbeat = asyncio.create_task(self._keep_lease(run_id), name=f"chat-lease:{run_id}")
        try:
            async with asyncio.timeout(max(0.0, remaining)):
                if point is None:
                    await self._turns(1, None, NO_BUDGET_USED)
                else:
                    await self._continue(point)
        except TimeoutError:
            await self.give_up(TurnFailure("AGENT_TIMEOUT"))
        except RunLost:
            logger.info("대화 턴을 놓았다 (취소됐거나 다른 작업이 이어받음) run=%s", run_id)
        finally:
            heartbeat.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await heartbeat

    async def _keep_lease(self, run_id: uuid.UUID) -> None:
        while True:
            await asyncio.sleep(LEASE_RENEW_SEC)
            try:
                await in_session(self._session_factory, run_service.renew_lease, run_id)
            except Exception:
                logger.exception("대화 실행 임대를 늘리지 못했다 run=%s", run_id)

    async def give_up(self, failure: TurnFailure) -> None:
        if self._run is None:
            return
        try:
            await in_session(self._session_factory, turn_service.fail, self._run, failure)
        except Exception:
            logger.exception("대화 턴 실패를 저장하지 못했다 run=%s", self._run.run_id)

    @property
    def _current(self) -> ClaimedRun:
        assert self._run is not None
        return self._run

    async def _turns(
        self, first_call: int, resume: AgentResumeDTO | None, budget: BudgetUsedDTO
    ) -> None:
        """AI 를 부르고, 행동을 기다리는 답이면 행동 결과를 실어 다시 부른다. 종결 답은 저장한다."""
        for turn_call in range(first_call, MAX_TURN_CALLS + 1):
            outcome = await self._call_agent(turn_call, resume, budget)
            if isinstance(outcome, TurnFailure):
                await self.give_up(outcome)
                return
            if outcome.view.status != "NEEDS_ACTION":
                await self._finish(turn_call, outcome)
                return
            if turn_call == MAX_TURN_CALLS:
                break
            follow_up = await self._take_action(turn_call, outcome)
            if follow_up is None:
                return
            resume, budget = follow_up
        # 응답 코드는 AI 오류와 같다. 호출 한도에 닿은 턴을 로그로 가른다
        logger.warning(
            "대화 턴이 호출 한도 %d번에 닿았다 run=%s", MAX_TURN_CALLS, self._current.run_id
        )
        await self.give_up(TurnFailure(UPSTREAM_ERROR))

    async def _call_agent(
        self, turn_call: int, resume: AgentResumeDTO | None, budget: BudgetUsedDTO
    ) -> TurnFinal | TurnFailure:
        """요청을 만들고 대화 자리를 잡아 보낸다. 보내기 직전에 보낸다는 표시를 저장한다."""
        try:
            request = await in_session(
                self._session_factory, request_service.build_request, self._current, resume, budget
            )
        except request_service.ContextTooLarge:
            return TurnFailure("AGENT_CONTEXT_TOO_LARGE")
        except request_service.PitchNotFound:
            # 범위 피치가 지워졌다. 자동 목표 확인 실행은 피치에 외래키가 없어 남는다.
            # 근거가 지워진 실행은 실패가 아니라 취소다(12-2 '삭제와 저장이 겹칠 때')
            await in_session(self._session_factory, run_service.cancel, self._current)
            raise RunLost(self._current.run_id) from None
        request_id = uuid.uuid4().hex
        overflow = request.history.overflow
        async with ai_slots.chat():
            await self._advance(
                turn_service.mark_sending,
                turn_call,
                request_id,
                request_service.offered_sources(request),
                request.memory.memory_version,
                overflow.prev_summary_version if overflow else None,
                [item.item_id for item in request.memory.profile or []],
            )
            return await request_turn(
                request,
                request_id=request_id,
                turn_call=turn_call,
                first_trigger=self._current.first_trigger,
                on_progress=partial(self._progress, turn_call),
            )

    async def _finish(self, turn_call: int, outcome: TurnFinal) -> None:
        await in_session(
            self._session_factory, turn_service.finish, self._current, turn_call, outcome
        )

    async def _take_action(
        self, turn_call: int, outcome: TurnFinal
    ) -> tuple[AgentResumeDTO, BudgetUsedDTO] | None:
        """기다리는 행동을 저장하고 실행해 다음 호출에 실을 값을 돌려준다. 승인을 기다리면 None."""
        await self._advance(turn_service.wait_for_action, turn_call, outcome)
        view = outcome.view
        assert view.state is not None
        pending = view.actions[0]
        action = pending.model_dump(mode="json")
        if pending.autonomy == "ASK" and pending.preapproved is None:
            result = await in_session(
                self._session_factory,
                action_service.hold_for_approval,
                self._current,
                action,
                view.profile_updates,
            )
            if result is None:
                return None
            action_ms = 0
        else:
            result, action_ms = await self._act(action)
        return _follow_up(view.state, result, view.budget_used, action_ms)

    async def _continue(self, point: turn_service.ResumePoint) -> None:
        result, action_ms = await self._act(point.action)
        resume, budget = _follow_up(
            SignedStateDTO.model_validate(point.state),
            result,
            BudgetUsedDTO.model_validate(point.budget_used),
            action_ms,
        )
        await self._turns(point.turn_call + 1, resume, budget)

    async def _act(self, action: dict[str, Any]) -> tuple[ActionResultDTO, int]:
        loop = asyncio.get_running_loop()
        started = loop.time()
        begun = await in_session(
            self._session_factory, action_service.begin_action, self._current, action
        )
        if isinstance(begun, ActionResultDTO):
            result = begun
        elif isinstance(begun, ReassessArgs):
            result = await self._reassess(action, begun)
        else:
            result = await in_session(
                self._session_factory, action_service.run_action, self._current, action, begun
            )
        return result, round((loop.time() - started) * 1000)

    async def _reassess(self, action: dict[str, Any], args: ReassessArgs) -> ActionResultDTO:
        prepared = await in_session(
            self._session_factory, reassess_service.prepare_reassess, self._current, action, args
        )
        if isinstance(prepared, ActionResultDTO):
            return prepared
        async with ai_slots.chat_pair():
            replies = await reassess_service.call_reassess(prepared)
        return await in_session(
            self._session_factory,
            reassess_service.finish_reassess,
            self._current,
            action,
            prepared,
            replies,
        )

    async def _progress(self, turn_call: int, line: StreamLine) -> None:
        saved = await in_session(
            self._session_factory, turn_service.save_progress, self._current, turn_call, line
        )
        if not saved:
            raise RunLost()

    async def _advance(self, fn: Callable[..., ClaimedRun | None], *args: Any) -> None:
        advanced = await in_session(self._session_factory, fn, self._current, *args)
        if advanced is None:
            raise RunLost()
        self._run = advanced


def _follow_up(
    state: SignedStateDTO, result: ActionResultDTO, budget: BudgetUsedDTO, action_ms: int
) -> tuple[AgentResumeDTO, BudgetUsedDTO]:
    """다음 호출에 실을 이어 부르기 값과 사용량. 행동에 걸린 시간을 사용 시간에 더한다."""
    resume = AgentResumeDTO(state=state, action_results=[result])
    return resume, budget.model_copy(update={"elapsed_ms": budget.elapsed_ms + action_ms})
