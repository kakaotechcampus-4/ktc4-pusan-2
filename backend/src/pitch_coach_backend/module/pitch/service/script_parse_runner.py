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

AI 를 부르는 동안에는 백그라운드 AI 자리(`core/ai_slots.py`) 하나를 쥔다. 자리가 없으면
SLOT_WAIT_SEC 까지만 기다리고, 넘으면 AI_UNAVAILABLE 로 실패시킨다. 기다린 시간도 PARSE_EXPIRE_AFTER
(90초)에 들어가므로, 기다림 + 최악 소요(61초)가 90초를 넘지 않게 묶는다.
"""

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable

from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from pitch_coach_backend.core import ai_slots
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

# 최악 소요 61초(script_parser.py)와 합쳐 만료(90초) 안에 끝나야 "만료"로 보인 작업이 뒤에서 도는
# 일이 없다
SLOT_WAIT_SEC = 20.0


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

    async def run(self, ticket: ParseTicket) -> None:
        script_id = ticket.script_version_id
        slots = ai_slots.background()
        try:
            async with asyncio.timeout(SLOT_WAIT_SEC):
                await slots.acquire()
        except TimeoutError:
            logger.warning("대본 파싱 AI 자리를 얻지 못함 script_version_id=%s", script_id)
            await self._save_failure(ticket, ScriptParseErrorCode.AI_UNAVAILABLE)
            return
        try:
            try:
                parsed = await self._parser.parse(ticket.script_text, ticket.target_ms)
            finally:
                # 실패 · 결과 저장과 평가 기준 요청은 자리 밖이다. 쥔 채 평가 기준이 자리를 잡으면
                # 서로 기다린다
                slots.release()
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

    def _fail(self, ticket: ParseTicket, code: ScriptParseErrorCode) -> bool:
        db = self._session_factory()
        try:
            return script_service.fail_parse(db, ticket, code)
        finally:
            db.close()
