import asyncio
import contextlib
import logging
import uuid
import weakref
from collections.abc import AsyncGenerator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from pitch_coach_backend.core.database import SessionLocal, in_session
from pitch_coach_backend.core.tasks import TaskRegistry
from pitch_coach_backend.module.chat.ai_client import TurnFailure, request_turn
from pitch_coach_backend.module.chat.ai_response import UPSTREAM_ERROR, StreamLine
from pitch_coach_backend.module.chat.dto import (
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

CHAT_SLOT_LIMIT = 4
MAX_TURN_CALLS = 6
STOP_TIMEOUT_SEC = 5.0
LEASE_RENEW_SEC = 10.0

# Callable[매개변수, 반환값] : 해당 함수 자체를 매개변수와 반환값으로 지정할 수 있다.
type SessionFactory = Callable[[], Session]
type ResumeWith = Callable[[Session, uuid.UUID], turn_service.ResumePoint | None]

_running = TaskRegistry()
_slots: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Semaphore] = (
    weakref.WeakKeyDictionary()
)
_slot_locks: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Lock] = (
    weakref.WeakKeyDictionary()
)


def _chat_slot() -> asyncio.Semaphore:
    loop = asyncio.get_running_loop()
    if (slots := _slots.get(loop)) is None:
        slots = asyncio.Semaphore(CHAT_SLOT_LIMIT)
        _slots[loop] = slots
    return slots


@asynccontextmanager
async def _double_slot() -> AsyncGenerator[None]:
    loop = asyncio.get_running_loop()
    slots = _chat_slot()
    lock = _slot_locks.setdefault(loop, asyncio.Lock())
    async with lock:
        await slots.acquire()
        try:
            await slots.acquire()
        except BaseException:
            slots.release()
            raise
    try:
        yield
    finally:
        slots.release()
        slots.release()


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
        logger.info("대화 턴을 멈춘다 (앱 종료) run=%s", run_id)
        raise
    except Exception:
        logger.exception("대화 턴 실패 run=%s", run_id)
        await turn.give_up(TurnFailure(UPSTREAM_ERROR))


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
                    await self._turns(1, None, BudgetUsedDTO())
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

    # AI 클라이언트 요청을 보내고, 응답을 스트리밍으로 받으며, 필요시 action을 실행하는 턴을 반복
    async def _turns(
        self, first_call: int, resume: AgentResumeDTO | None, budget: BudgetUsedDTO
    ) -> None:
        for turn_call in range(first_call, MAX_TURN_CALLS + 1):
            try:
                request = await in_session(
                    self._session_factory,
                    request_service.build_request,
                    self._current,
                    resume,
                    budget,
                )
            except request_service.ContextTooLarge:
                await self.give_up(TurnFailure("AGENT_CONTEXT_TOO_LARGE"))
                return
            request_id = uuid.uuid4().hex
            async with _chat_slot():
                await self._advance(
                    turn_service.mark_sending,
                    turn_call,
                    request_id,
                    request_service.offered_sources(request),
                    request.memory.memory_version,
                    request.history.overflow.prev_summary_version
                    if request.history.overflow
                    else None,
                )
                outcome = await request_turn(
                    request,
                    request_id=request_id,
                    turn_call=turn_call,
                    first_trigger=self._current.first_trigger,
                    on_progress=lambda line, call=turn_call: self._progress(call, line),
                )
            if isinstance(outcome, TurnFailure):
                await self.give_up(outcome)
                return
            if outcome.view.status != "NEEDS_ACTION":
                await in_session(
                    self._session_factory, turn_service.finish, self._current, turn_call, outcome
                )
                return
            if turn_call == MAX_TURN_CALLS:
                break
            await self._advance(turn_service.wait_for_action, turn_call, outcome)
            action = outcome.data["actions"][0]
            if action["autonomy"] == "ASK" and action.get("preapproved") is None:
                result = await in_session(
                    self._session_factory,
                    action_service.hold_for_approval,
                    self._current,
                    action,
                    outcome.data.get("profile_updates"),
                )
                if result is None:
                    return
                action_ms = 0
            else:
                result, action_ms = await self._act(action)
            resume = AgentResumeDTO(
                state=SignedStateDTO.model_validate(outcome.data["state"]),
                action_results=[result],
            )
            budget = BudgetUsedDTO.model_validate(outcome.data["budget_used"])
            budget = budget.model_copy(update={"elapsed_ms": budget.elapsed_ms + action_ms})
        await self.give_up(TurnFailure(UPSTREAM_ERROR))

    async def _continue(self, point: turn_service.ResumePoint) -> None:
        result, action_ms = await self._act(point.action)
        budget = BudgetUsedDTO.model_validate(point.budget_used)
        await self._turns(
            point.turn_call + 1,
            AgentResumeDTO(
                state=SignedStateDTO.model_validate(point.state), action_results=[result]
            ),
            budget.model_copy(update={"elapsed_ms": budget.elapsed_ms + action_ms}),
        )

    async def _act(self, action: dict[str, Any]) -> tuple[ActionResultDTO, int]:
        loop = asyncio.get_running_loop()
        started = loop.time()
        result = await in_session(
            self._session_factory, action_service.begin_action, self._current, action
        )
        if result is None:
            if action["type"] == "REASSESS":
                result = await self._reassess(action)
            else:
                result = await in_session(
                    self._session_factory, action_service.run_action, self._current, action
                )
        return result, round((loop.time() - started) * 1000)

    async def _reassess(self, action: dict[str, Any]) -> ActionResultDTO:
        prepared = await in_session(
            self._session_factory, reassess_service.prepare_reassess, self._current, action
        )
        if isinstance(prepared, ActionResultDTO):
            return prepared
        async with _double_slot():
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
