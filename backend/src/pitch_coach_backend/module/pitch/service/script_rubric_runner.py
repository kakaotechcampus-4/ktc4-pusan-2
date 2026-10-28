"""장별 평가 기준 만들기 백그라운드 작업. 대본 파싱 결과를 저장한 뒤 파싱 작업이 이어서 부른다.

    파싱 결과와 함께 PENDING ──▶ /scripts/rubric 모든 장 ──▶ 장별 첫 성공 저장
      ──▶ (재시도 1회) ──▶ DONE · PARTIAL · FAILED

재시도는 버전마다 한 번이다 (architecture.md 1-7 · 3절).
  - 최초 200 의 failed 중 LLM 오류 장만 slide_numbers 로 다시 부른다
  - 최초 호출이 무응답 · 408 · 429 · 5xx 면 failed 를 모르니 전체를 다시 부른다
  - 보내기 전에 rubric_retry_used 를 커밋한다. 다시 부른 뒤에는 더 부르지 않는다

동시에 AI 로 보내는 대본 등록은 2개까지이고, 다른 백그라운드 AI 호출과 합계도 센다
(`core/ai_slots.py`). 자리가 없으면 기다렸다 보낸다. AI 의 120초는 보낸 때부터라 기다린 시간은
실패로 이어지지 않는다.

지키는 규칙은 script_parse_runner 와 같다. AI 를 기다리는 동안 DB 세션을 쥐지 않고, 저장할 때마다
짧은 세션을 threadpool 에서 연다. 저장 · 재시도 기록 · 상태 확정은 모두 작업의 표식
(rubric_requested_at)이 그대로일 때만 반영돼, 이어받은 뒤 늦게 끝난 옛 작업은 아무것도
바꾸지 못한다.

실패는 밖으로 던지지 않고 로그만 남긴다. DB 가 안 돼 중간에 멈춘 작업은 PENDING 으로 남고,
BE 가 다시 뜰 때(resume_all) 남은 한도로 이어 간다.
"""

import logging
import uuid
from collections.abc import Callable, Sequence
from contextvars import ContextVar
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from pitch_coach_backend.core import ai_slots
from pitch_coach_backend.core.database import SessionLocal
from pitch_coach_backend.core.logging import sqlstate
from pitch_coach_backend.module.pitch.adapter.script_rubric import (
    RETRYABLE_SLIDE_CODES,
    RubricCallError,
    RubricClient,
    RubricReply,
)
from pitch_coach_backend.module.pitch.dto import RubricTicket
from pitch_coach_backend.module.pitch.service import script as script_service

logger = logging.getLogger(__name__)
# 작업마다 마지막 AI 요청을 추적한다. 동시에 여러 대본이 실행돼도 서로 섞이지 않는다.
_request_id: ContextVar[str | None] = ContextVar("rubric_request_id", default=None)
_LOG_FAILED_CODES = RETRYABLE_SLIDE_CODES | frozenset(
    {"RUBRIC_MISSING", "RUBRIC_MISMATCH", "STT_TEXT_MISSING", "VALIDATION_ERROR", "INTERNAL_ERROR"}
)


class ScriptRubricRunner:
    def __init__(
        self, client: RubricClient, session_factory: Callable[[], Session] = SessionLocal
    ) -> None:
        self._client = client
        self._session_factory = session_factory

    async def start(self, script_version_id: uuid.UUID, requested_at: datetime) -> None:
        """파싱 결과를 저장한 뒤 그 파싱 작업의 표식(`requested_at`)으로 부른다.

        complete_parse 가 그 표식으로 PENDING 을 둔 버전만 돈다. 이미 시작했던 버전이면 아무것도
        하지 않는다.
        """
        try:
            ticket = await self._db(script_service.begin_rubric, script_version_id, requested_at)
        except Exception as exc:
            logger.error(
                "평가 기준 작업 시작 실패 script_version_id=%s requested_at=%s "
                "error=%s sqlstate=%s",
                script_version_id,
                requested_at,
                type(exc).__name__,
                sqlstate(exc),
            )
            return
        if ticket is not None:
            await self._run(ticket)

    async def resume_all(self) -> None:
        """BE 가 뜰 때 부른다. PENDING 으로 남은 작업을 하나씩 이어 간다."""
        try:
            tickets = await self._db(script_service.resume_rubrics)
        except Exception as exc:
            logger.error(
                "남은 평가 기준 작업을 읽지 못했다 error=%s sqlstate=%s",
                type(exc).__name__,
                sqlstate(exc),
            )
            return
        if tickets:
            logger.info("평가 기준 작업 %d개를 이어받았다", len(tickets))
        for ticket in tickets:
            await self._run(ticket)

    async def _run(self, ticket: RubricTicket) -> None:
        token = _request_id.set(None)
        try:
            if ticket.resumed:
                await self._resume(ticket)
            else:
                await self._first(ticket)
            status = await self._db(script_service.finish_rubric, ticket)
        except Exception as exc:
            # SQLAlchemy 예외에는 SQL 매개변수·DB 오류 상세가 있어 traceback도 남기지 않는다.
            logger.error(
                "평가 기준 작업 실패, PENDING 으로 남김 script_version_id=%s requested_at=%s "
                "request_id=%s error=%s sqlstate=%s",
                ticket.script_version_id,
                ticket.requested_at,
                _request_id.get(),
                type(exc).__name__,
                sqlstate(exc),
            )
            return
        finally:
            _request_id.reset(token)
        if status is None:
            logger.info("지난 평가 기준 작업을 버림 script_version_id=%s", ticket.script_version_id)
        else:
            logger.info(
                "평가 기준 작업 끝 script_version_id=%s status=%s",
                ticket.script_version_id,
                status,
            )

    async def _first(self, ticket: RubricTicket) -> None:
        # 전체 재시도는 같은 요청을 다시 보내는 것이라 처음과 같은 요청 ID 를 쓴다
        # (architecture.md 2절)
        request_id = uuid.uuid4().hex
        try:
            reply = await self._call(ticket, None, request_id)
        except RubricCallError as exc:
            if not exc.retryable or not await self._db(script_service.use_rubric_retry, ticket):
                return
            try:
                reply = await self._call(ticket, None, request_id)
            except RubricCallError:
                return
            await self._save(ticket, reply)
            return

        if not await self._save(ticket, reply):
            return
        retry = sorted(
            number
            for number, code in reply.failed.items()
            if code in RETRYABLE_SLIDE_CODES
            and number in ticket.missing
            and number not in reply.rubrics
        )
        if not retry or not await self._db(script_service.use_rubric_retry, ticket):
            return
        try:
            again = await self._call(ticket, retry, uuid.uuid4().hex)
        except RubricCallError:
            # 최초 응답에서 저장한 기준은 그대로 둔다
            return
        await self._save(ticket, again)

    async def _resume(self, ticket: RubricTicket) -> None:
        """앞 작업이 어디까지 보냈는지 모르니 남은 한도(재시도)로 기준이 없는 장만 다시 부른다."""
        if ticket.retry_used or not ticket.missing:
            return
        if not await self._db(script_service.use_rubric_retry, ticket):
            return
        numbers = None if len(ticket.missing) == len(ticket.slides) else ticket.missing
        try:
            reply = await self._call(ticket, numbers, uuid.uuid4().hex)
        except RubricCallError:
            return
        await self._save(ticket, reply)

    async def _call(
        self, ticket: RubricTicket, slide_numbers: Sequence[int] | None, request_id: str
    ) -> RubricReply:
        _request_id.set(request_id)
        try:
            # 시도 시간 제한은 client 안에서 건다. 자리를 기다린 시간은 들어가지 않는다
            async with ai_slots.rubric():
                reply = await self._client.generate(
                    pitch_id=str(ticket.pitch_id),
                    script_version_id=str(ticket.script_version_id),
                    slides=ticket.slides,
                    slide_numbers=slide_numbers,
                    request_id=request_id,
                )
        except RubricCallError as exc:
            # 대본 원문은 로그에 남기지 않는다
            logger.warning(
                "평가 기준 호출 실패 script_version_id=%s request_id=%s slide_numbers=%s "
                "status=%s ai_error_code=%s error=%s",
                ticket.script_version_id,
                request_id,
                slide_numbers,
                exc.status_code,
                exc.ai_error_code,
                type(exc).__name__,
            )
            raise
        if reply.failed:
            logger.info(
                "평가 기준 일부 장 실패 script_version_id=%s request_id=%s failed=%s",
                ticket.script_version_id,
                request_id,
                {
                    number: code if code in _LOG_FAILED_CODES else "UNKNOWN"
                    for number, code in reply.failed.items()
                },
            )
        return reply

    async def _save(self, ticket: RubricTicket, reply: RubricReply) -> bool:
        return await self._db(script_service.save_rubrics, ticket, reply.rubrics, reply.meta)

    async def _db(self, step: Callable[..., Any], *args: Any) -> Any:
        """service 함수 하나를 짧은 세션으로 threadpool 에서 돌린다."""
        return await run_in_threadpool(self._in_session, step, *args)

    def _in_session(self, step: Callable[..., Any], *args: Any) -> Any:
        db = self._session_factory()
        try:
            return step(db, *args)
        finally:
            db.close()
