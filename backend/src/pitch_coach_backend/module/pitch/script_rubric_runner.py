"""장별 평가 기준 만들기 백그라운드 작업. 대본 파싱 결과를 저장한 뒤 파싱 작업이 이어서 부른다.

    파싱 결과와 함께 PENDING ──▶ /scripts/rubric 모든 장 ──▶ 장별 첫 성공 저장
      ──▶ (재시도 1회) ──▶ DONE · PARTIAL · FAILED

재시도는 버전마다 한 번이다 (architecture.md 1-7 · 3절).
  - 최초 200 의 failed 중 LLM 오류 장만 slide_numbers 로 다시 부른다
  - 최초 호출이 무응답 · 408 · 429 · 5xx 면 failed 를 모르니 전체를 다시 부른다
  - 보내기 전에 rubric_retry_used 를 커밋한다. 다시 부른 뒤에는 더 부르지 않는다

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
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from pitch_coach_backend.core.database import SessionLocal
from pitch_coach_backend.module.pitch import service as pitch_service
from pitch_coach_backend.module.pitch.dto import RubricTicket
from pitch_coach_backend.module.pitch.script_rubric import (
    RETRYABLE_SLIDE_CODES,
    RubricCallError,
    RubricClient,
    RubricReply,
)

logger = logging.getLogger(__name__)


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
            ticket = await self._db(pitch_service.begin_rubric, script_version_id, requested_at)
        except Exception:
            logger.exception("평가 기준 작업 시작 실패 script_version_id=%s", script_version_id)
            return
        if ticket is not None:
            await self._run(ticket)

    async def resume_all(self) -> None:
        """BE 가 뜰 때 부른다. PENDING 으로 남은 작업을 하나씩 이어 간다."""
        try:
            tickets = await self._db(pitch_service.resume_rubrics)
        except Exception:
            logger.exception("남은 평가 기준 작업을 읽지 못했다")
            return
        if tickets:
            logger.info("평가 기준 작업 %d개를 이어받았다", len(tickets))
        for ticket in tickets:
            await self._run(ticket)

    async def _run(self, ticket: RubricTicket) -> None:
        try:
            if ticket.resumed:
                await self._resume(ticket)
            else:
                await self._first(ticket)
            status = await self._db(pitch_service.finish_rubric, ticket)
        except Exception:
            logger.exception(
                "평가 기준 작업 실패, PENDING 으로 남김 script_version_id=%s",
                ticket.script_version_id,
            )
            return
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
            if not exc.retryable or not await self._db(pitch_service.use_rubric_retry, ticket):
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
        if not retry or not await self._db(pitch_service.use_rubric_retry, ticket):
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
        if not await self._db(pitch_service.use_rubric_retry, ticket):
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
        try:
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
                "평가 기준 호출 실패 script_version_id=%s request_id=%s slide_numbers=%s: %s",
                ticket.script_version_id,
                request_id,
                slide_numbers,
                exc,
            )
            raise
        if reply.failed:
            logger.info(
                "평가 기준 일부 장 실패 script_version_id=%s request_id=%s failed=%s",
                ticket.script_version_id,
                request_id,
                reply.failed,
            )
        return reply

    async def _save(self, ticket: RubricTicket, reply: RubricReply) -> bool:
        return await self._db(pitch_service.save_rubrics, ticket, reply.rubrics, reply.meta)

    async def _db(self, step: Callable[..., Any], *args: Any) -> Any:
        """service 함수 하나를 짧은 세션으로 threadpool 에서 돌린다."""
        return await run_in_threadpool(self._in_session, step, *args)

    def _in_session(self, step: Callable[..., Any], *args: Any) -> Any:
        db = self._session_factory()
        try:
            return step(db, *args)
        finally:
            db.close()
