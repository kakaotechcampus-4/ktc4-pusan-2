"""대본 파싱 백그라운드 작업. 업로드·재시도 응답을 돌려준 뒤 FastAPI BackgroundTasks 로 돈다.

    ParseTicket ──▶ AI 호출 (await, DB 세션 없음) ──▶ 짧은 세션: DONE / FAILED 저장
                                                     ──▶ (DONE 이면) 장별 평가 기준 작업

지키는 규칙 (BE 는 uvicorn 워커 하나의 이벤트 루프를 실시간 STT WebSocket 과 같이 쓴다)
  - AI 는 `await` 로 기다린다. 기다리는 동안 루프는 다른 요청과 STT 를 계속 처리한다
  - AI 를 기다리는 동안 DB 세션을 열지 않는다. 열어 두면 업로드 수만큼 커넥션이 수십 초씩
    묶여 풀(기본 5 + 15)이 마른다. 대본 텍스트는 요청에서 이미 읽어 ticket 에 담아 온다
  - 요청의 `get_db` 세션을 쓰지 않는다. FastAPI 는 백그라운드 작업 전에 그 세션을 닫는다.
    그래서 저장할 때 새 세션을 열고, 동기 SQLAlchemy 라 threadpool 에서 돌린다
    (realtime/transcript_store.py 와 같은 방식)

실패는 밖으로 던지지 않는다. 응답은 이미 나갔으니 받을 사람이 없고, 던지면 ASGI 로그만 남는다.
AI 실패는 FAILED 로 저장한다. 결과 저장이 실패하면(값 문제 등) 새 세션으로 FAILED(INTERNAL_ERROR)
를 한 번 더 시도한다 — 안 그러면 FE 가 90초 동안 "분석 중"을 보다가 엉뚱한 PARSE_EXPIRED 를 받는다.
그마저 실패하면(DB 장애) 로그만 남긴다. 그 행은 PENDING 으로 남지만 script.PARSE_EXPIRE_AFTER 가
지나면 FAILED(PARSE_EXPIRED) 로 보여 재시도할 수 있다.

BE 가 재시작돼 작업이 사라지면 뜰 때 resume_all 이 만료 전 PENDING 을 이어받는다.
"""

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from pitch_coach_backend.core.database import SessionLocal
from pitch_coach_backend.core.logging import sqlstate
from pitch_coach_backend.module.pitch.adapter.script_parser import (
    ParsedScript,
    ScriptParseError,
    ScriptParser,
)
from pitch_coach_backend.module.pitch.dto import ParseSaved, ParseTicket, ScriptParseErrorCode
from pitch_coach_backend.module.pitch.service import script as script_service
from pitch_coach_backend.module.pitch.service.script_rubric_runner import ScriptRubricRunner

logger = logging.getLogger(__name__)


class ScriptParseRunner:
    def __init__(
        self,
        parser: ScriptParser,
        session_factory: Callable[[], Session] = SessionLocal,
        on_parsed: Callable[[uuid.UUID], Awaitable[None]] | None = None,
        rubric_runner: ScriptRubricRunner | None = None,
    ) -> None:
        self._parser = parser
        self._session_factory = session_factory
        # 파싱 결과를 저장한 뒤 부른다 — 다음 Take 의 코칭 계획을 요청한다 (coach-agent.md 5-1)
        self._on_parsed = on_parsed
        # 이어서 장별 평가 기준을 만든다 (architecture.md 1-7)
        self._rubric_runner = rubric_runner

    async def resume_all(self, *, started_before: datetime | None = None) -> None:
        """BE 가 뜰 때 부른다. 만료 전 PENDING 으로 남은 파싱을 이어 간다.

        이어받으며 만료 시각을 다시 센 작업들이라 차례로 돌리면 뒤의 것이 만료된다. 한꺼번에 띄운다.
        """
        # threadpool 에서 DB 를 읽을 때는 이미 새 업로드가 시작됐을 수 있다. 기준 시각은
        # 첫 await 전에 고정한다. lifespan 은 요청을 받기 전의 앱 시작 시각을 넘긴다
        if started_before is None:
            started_before = datetime.now(UTC)
        try:
            tickets = await run_in_threadpool(self._resume, started_before)
        except Exception as exc:
            logger.error(
                "남은 대본 파싱 작업을 읽지 못했다 error=%s sqlstate=%s",
                type(exc).__name__,
                sqlstate(exc),
            )
            return
        if tickets:
            logger.info("대본 파싱 작업 %d개를 이어받았다", len(tickets))
        # run 은 실패를 던지지 않는다
        await asyncio.gather(*(self.run(ticket) for ticket in tickets))

    async def run(self, ticket: ParseTicket) -> None:
        script_id = ticket.script_version_id
        try:
            parsed = await self._parser.parse(ticket.script_text, ticket.target_ms)
        except ScriptParseError as exc:
            # 대본 원문은 로그에 남기지 않는다 (사용자 콘텐츠)
            logger.warning(
                "대본 파싱 실패 script_version_id=%s code=%s status=%s ai_code=%s request_id=%s",
                script_id,
                exc.code,
                exc.status_code,
                exc.ai_error_code,
                exc.request_id,
            )
            await self._save_failure(ticket, exc.code)
            return
        except Exception as exc:
            # 예외 메시지에 AI 응답이나 대본 조각이 섞일 수 있어 종류만 남긴다
            logger.error(
                "대본 파싱 중 예상 못 한 오류 script_version_id=%s error=%s",
                script_id,
                type(exc).__name__,
            )
            await self._save_failure(ticket, ScriptParseErrorCode.INTERNAL_ERROR)
            return

        await self._save_result(ticket, parsed)

    async def _save_result(self, ticket: ParseTicket, parsed: ParsedScript) -> None:
        script_id = ticket.script_version_id
        try:
            saved = await run_in_threadpool(self._complete, ticket, parsed)
        except Exception as exc:
            # SQLAlchemy 예외 본문에는 full_content 같은 SQL 매개변수와 DB 오류 메시지가 붙어
            # 대본 원문이 섞인다. traceback 없이 예외 종류 · SQLSTATE · 요청 ID 만 남긴다
            logger.error(
                "대본 파싱 결과 저장 실패 script_version_id=%s request_id=%s error=%s sqlstate=%s",
                script_id,
                parsed.request_id,
                type(exc).__name__,
                sqlstate(exc),
            )
            # 실패한 세션은 닫혔다(롤백). 새 세션으로 실패를 남긴다. 표식 조건이 있어 안전하다
            await self._save_failure(ticket, ScriptParseErrorCode.INTERNAL_ERROR)
            return
        if saved is ParseSaved.STALE:
            # 그 사이 재시도가 새 작업을 시작했거나 이미 끝난 행이다. 새 작업의 결과를 지킨다
            logger.info("지난 대본 파싱 작업의 결과를 버림 script_version_id=%s", script_id)
            return
        if saved is ParseSaved.MISMATCH:
            # 저장본과 달라 적용하지 않았다. 바뀐 것이 없으니 계획도 평가 기준도 만들지 않는다
            return
        if self._on_parsed is not None and ticket.pitch_id is not None:
            try:
                await self._on_parsed(ticket.pitch_id)
            except Exception:
                # 계획이 없어도 코칭은 돈다. 파싱 결과는 이미 저장했다
                logger.exception("대본 파싱 뒤 코칭 계획 요청 실패 pitch=%s", ticket.pitch_id)
        # 저장본을 재사용했으면 기준을 만든 적 없는 버전일 때만 돈다 (complete_parse 가 PENDING 을
        # 두었을 때)
        if self._rubric_runner is not None:
            await self._rubric_runner.start(script_id, ticket.requested_at)

    async def _save_failure(self, ticket: ParseTicket, code: ScriptParseErrorCode) -> None:
        try:
            await run_in_threadpool(self._fail, ticket, code)
        except Exception as exc:
            logger.error(
                "대본 파싱 실패 저장 실패 script_version_id=%s code=%s error=%s sqlstate=%s",
                ticket.script_version_id,
                code,
                type(exc).__name__,
                sqlstate(exc),
            )

    # ── threadpool 에서 도는 부분. 저장할 때마다 짧은 세션을 열고 닫는다 ──

    def _complete(self, ticket: ParseTicket, parsed: ParsedScript) -> ParseSaved:
        db = self._session_factory()
        try:
            return script_service.complete_parse(db, ticket, parsed)
        finally:
            db.close()

    def _resume(self, started_before: datetime) -> list[ParseTicket]:
        db = self._session_factory()
        try:
            return script_service.resume_parses(db, started_before=started_before)
        finally:
            db.close()

    def _fail(self, ticket: ParseTicket, code: ScriptParseErrorCode) -> bool:
        db = self._session_factory()
        try:
            return script_service.fail_parse(db, ticket, code)
        finally:
            db.close()
