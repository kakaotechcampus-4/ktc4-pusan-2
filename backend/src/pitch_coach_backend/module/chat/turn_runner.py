import asyncio
import logging
import uuid
import weakref
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from pitch_coach_backend.core.database import SessionLocal
from pitch_coach_backend.core.tasks import TaskRegistry
from pitch_coach_backend.module.chat.ai_client import TurnFailure, TurnFinal, request_turn
from pitch_coach_backend.module.chat.ai_response import UPSTREAM_ERROR, StreamLine
from pitch_coach_backend.module.chat.dto import (
    ActionResultDTO,
    AgentResumeDTO,
    AgentTrigger,
    AgentTurnRequestDTO,
    BudgetUsedDTO,
    SignedStateDTO,
)

logger = logging.getLogger(__name__)

CHAT_SLOT_LIMIT = 4
MAX_TURN_CALLS = 6
STOP_TIMEOUT_SEC = 5.0

# Callable[매개변수, 반환값] : 해당 함수 자체를 매개변수와 반환값으로 지정할 수 있다.
type SessionFactory = Callable[[], Session]

_running = TaskRegistry()
_slots: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Semaphore] = (
    weakref.WeakKeyDictionary()
)


@dataclass(frozen=True)
class ClaimedRun:
    run_id: uuid.UUID
    first_trigger: AgentTrigger
    row_revision: int
    deadline: datetime


class TurnStore(Protocol):
    def claim(self, db: Session, run_id: uuid.UUID) -> ClaimedRun | None: ...

    def build_request(
        self,
        db: Session,
        run: ClaimedRun,
        resume: AgentResumeDTO | None,
        budget_used: BudgetUsedDTO,
    ) -> AgentTurnRequestDTO: ...

    def mark_sending(
        self, db: Session, run: ClaimedRun, turn_call: int, request_id: str
    ) -> ClaimedRun | None: ...

    def save_progress(
        self, db: Session, run: ClaimedRun, turn_call: int, line: StreamLine
    ) -> bool: ...

    def wait_for_action(
        self, db: Session, run: ClaimedRun, turn_call: int, final: TurnFinal
    ) -> ClaimedRun | None: ...

    def finish(self, db: Session, run: ClaimedRun, turn_call: int, final: TurnFinal) -> bool: ...

    def fail(self, db: Session, run: ClaimedRun, failure: TurnFailure) -> bool: ...

    async def execute_action(self, run: ClaimedRun, action: dict[str, Any]) -> ActionResultDTO: ...


class _RunLost(Exception):
    pass


def _chat_slot() -> asyncio.Semaphore:
    loop = asyncio.get_running_loop()
    if (slots := _slots.get(loop)) is None:
        slots = asyncio.Semaphore(CHAT_SLOT_LIMIT)
        _slots[loop] = slots
    return slots


async def _in_session[T](session_factory: SessionFactory, fn: Callable[..., T], *args: Any) -> T:
    def call() -> T:
        db = session_factory()
        try:
            return fn(db, *args)
        finally:
            db.close()

    return await run_in_threadpool(call)


def start(
    run_id: uuid.UUID, store: TurnStore, session_factory: SessionFactory = SessionLocal
) -> asyncio.Task[None] | None:
    if _running.running(run_id):
        return None
    return _running.spawn(
        run(run_id, store, session_factory), name=f"chat-turn:{run_id}", key=run_id
    )


async def run(
    run_id: uuid.UUID, store: TurnStore, session_factory: SessionFactory = SessionLocal
) -> None:
    turn = _Turn(store, session_factory)
    try:
        await turn.run(run_id)
    except asyncio.CancelledError:
        logger.info("대화 턴을 멈춘다 (앱 종료) run=%s", run_id)
        raise
    except Exception:
        logger.exception("대화 턴 실패 run=%s", run_id)
        await turn.give_up(TurnFailure(UPSTREAM_ERROR))


async def stop_all(timeout: float = STOP_TIMEOUT_SEC) -> None:
    await _running.stop_all(timeout)


class _Turn:
    def __init__(self, store: TurnStore, session_factory: SessionFactory) -> None:
        self._store = store
        self._session_factory = session_factory
        self._run: ClaimedRun | None = None

    async def run(self, run_id: uuid.UUID) -> None:
        self._run = await _in_session(self._session_factory, self._store.claim, run_id)
        if self._run is None:
            return
        remaining = (self._run.deadline - datetime.now(UTC)).total_seconds()
        try:
            async with asyncio.timeout(max(0.0, remaining)):
                await self._turns()
        except TimeoutError:
            await self.give_up(TurnFailure("AGENT_TIMEOUT"))
        except _RunLost:
            logger.info("대화 턴을 놓았다 (취소됐거나 다른 작업이 이어받음) run=%s", run_id)

    async def give_up(self, failure: TurnFailure) -> None:
        if self._run is None:
            return
        try:
            await _in_session(self._session_factory, self._store.fail, self._run, failure)
        except Exception:
            logger.exception("대화 턴 실패를 저장하지 못했다 run=%s", self._run.run_id)

    @property
    def _current(self) -> ClaimedRun:
        assert self._run is not None
        return self._run

    async def _turns(self) -> None:
        resume: AgentResumeDTO | None = None
        budget = BudgetUsedDTO()
        loop = asyncio.get_running_loop()
        for turn_call in range(1, MAX_TURN_CALLS + 1):
            request = await _in_session(
                self._session_factory, self._store.build_request, self._current, resume, budget
            )
            request_id = uuid.uuid4().hex
            async with _chat_slot():
                await self._advance(self._store.mark_sending, turn_call, request_id)
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
                await _in_session(
                    self._session_factory, self._store.finish, self._current, turn_call, outcome
                )
                return
            if turn_call == MAX_TURN_CALLS:
                break
            await self._advance(self._store.wait_for_action, turn_call, outcome)
            started = loop.time()
            result = await self._store.execute_action(self._current, outcome.data["actions"][0])
            action_ms = round((loop.time() - started) * 1000)
            resume = AgentResumeDTO(
                state=SignedStateDTO.model_validate(outcome.data["state"]),
                action_results=[result],
            )
            budget = BudgetUsedDTO.model_validate(outcome.data["budget_used"])
            budget = budget.model_copy(update={"elapsed_ms": budget.elapsed_ms + action_ms})
        await self.give_up(TurnFailure(UPSTREAM_ERROR))

    async def _progress(self, turn_call: int, line: StreamLine) -> None:
        saved = await _in_session(
            self._session_factory, self._store.save_progress, self._current, turn_call, line
        )
        if not saved:
            raise _RunLost()

    async def _advance(self, fn: Callable[..., ClaimedRun | None], *args: Any) -> None:
        advanced = await _in_session(self._session_factory, fn, self._current, *args)
        if advanced is None:
            raise _RunLost()
        self._run = advanced
