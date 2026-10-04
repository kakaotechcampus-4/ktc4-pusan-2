"""대본 파싱 백그라운드 작업. 업로드·재시도 응답을 돌려준 뒤 FastAPI BackgroundTasks 로 돈다.

    ParseTicket ──▶ AI 호출 (await, DB 세션 없음) ──▶ 짧은 세션: DONE / FAILED 저장

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
그마저 실패하면(DB 장애) 로그만 남긴다. 그 행은 PENDING 으로 남지만 service.PARSE_EXPIRE_AFTER 가
지나면 FAILED(PARSE_EXPIRED) 로 보여 재시도할 수 있다.
"""

import logging
from collections.abc import Callable

from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from pitch_coach_backend.core.database import SessionLocal
from pitch_coach_backend.module.pitch import service as pitch_service
from pitch_coach_backend.module.pitch.dto import ParseTicket, ScriptParseErrorCode
from pitch_coach_backend.module.pitch.script_parser import (
    ParsedScript,
    ScriptParseError,
    ScriptParser,
)

logger = logging.getLogger(__name__)


class ScriptParseRunner:
    def __init__(
        self, parser: ScriptParser, session_factory: Callable[[], Session] = SessionLocal
    ) -> None:
        self._parser = parser
        self._session_factory = session_factory

    async def run(self, ticket: ParseTicket) -> None:
        script_id = ticket.script_version_id
        try:
            parsed = await self._parser.parse(ticket.script_text)
        except ScriptParseError as exc:
            # 대본 원문은 로그에 남기지 않는다 (사용자 콘텐츠)
            logger.warning("대본 파싱 실패 script_version_id=%s: %s", script_id, exc)
            await self._save_failure(ticket, exc.code)
            return
        except Exception:
            logger.exception("대본 파싱 중 예상 못 한 오류 script_version_id=%s", script_id)
            await self._save_failure(ticket, ScriptParseErrorCode.INTERNAL_ERROR)
            return

        await self._save_result(ticket, parsed)

    async def _save_result(self, ticket: ParseTicket, parsed: ParsedScript) -> None:
        script_id = ticket.script_version_id
        try:
            saved = await run_in_threadpool(self._complete, ticket, parsed)
        except Exception:
            logger.exception("대본 파싱 결과 저장 실패 script_version_id=%s", script_id)
            # 실패한 세션은 닫혔다(롤백). 새 세션으로 실패를 남긴다. 표식 조건이 있어 안전하다
            await self._save_failure(ticket, ScriptParseErrorCode.INTERNAL_ERROR)
            return
        if not saved:
            # 그 사이 재시도가 새 작업을 시작했거나 이미 끝난 행이다. 새 작업의 결과를 지킨다
            logger.info("지난 대본 파싱 작업의 결과를 버림 script_version_id=%s", script_id)

    async def _save_failure(self, ticket: ParseTicket, code: ScriptParseErrorCode) -> None:
        try:
            await run_in_threadpool(self._fail, ticket, code)
        except Exception:
            logger.exception(
                "대본 파싱 실패 저장 실패 script_version_id=%s", ticket.script_version_id
            )

    # ── threadpool 에서 도는 부분. 저장할 때마다 짧은 세션을 열고 닫는다 ──

    def _complete(self, ticket: ParseTicket, parsed: ParsedScript) -> bool:
        db = self._session_factory()
        try:
            return pitch_service.complete_parse(db, ticket, parsed)
        finally:
            db.close()

    def _fail(self, ticket: ParseTicket, code: ScriptParseErrorCode) -> bool:
        db = self._session_factory()
        try:
            return pitch_service.fail_parse(db, ticket, code)
        finally:
            db.close()
