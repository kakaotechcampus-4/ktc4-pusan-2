"""WebSocket 연결 하나를 인증하고 Take 의 STT 스트림에 붙인다.

    FE ──(WS-1)── RealtimeSession ──push──▶ TakeStream ──(WS-2)── Deepgram

이 클래스는 **연결만큼만** 산다. 오디오 파이프라인과 Deepgram 세션은 `runtime.TakeStream`
이 들고 있고 연결보다 오래 남는다 — 탭을 새로고침해도 세그먼트 번호가 이어지도록.

연결 하나의 순서: Origin → auth(JWT) → 인가(DB 1회: 사용자·Take 존재·소유·상태·대본 용어.
READY 면 RUNNING 으로 바꾸고 이 Take 에 쓸 값을 고정한 뒤 코치가 들고 있을 값을 읽는다) → ready →
오디오 · 1초 측정값(metrics) · 장 변경(slide) → stop(마지막 전사 · 측정값까지 저장한 뒤 ANALYZING ·
FINALIZING 으로 적고 Take 결과를 띄운다).
"""

import contextlib
import logging
import uuid
from collections.abc import Awaitable
from typing import cast
from urllib.parse import urlsplit

import anyio
from fastapi import WebSocket, WebSocketDisconnect
from pydantic import BaseModel, ValidationError
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from pitch_coach_backend.coach import store as coach_store
from pitch_coach_backend.coach.client import CoachClient
from pitch_coach_backend.coach.requests import TakeContext
from pitch_coach_backend.coach.store import CoachStore
from pitch_coach_backend.core.config import settings
from pitch_coach_backend.core.database import is_transient_db_error
from pitch_coach_backend.core.exceptions import UnauthorizedException
from pitch_coach_backend.core.security import decode_access_token
from pitch_coach_backend.module.pitch.service import script as script_service
from pitch_coach_backend.module.take.service import lifecycle as lifecycle_service
from pitch_coach_backend.module.take.service import take as take_service
from pitch_coach_backend.module.user import service as user_service
from pitch_coach_backend.realtime.dto import (
    TAKE_END_REASONS,
    AuthMessage,
    AutoEndMessage,
    AutoEndReason,
    ErrorMessage,
    MetricsMessage,
    ReadyMessage,
    SlideMessage,
    StopMessage,
    TakeEndReason,
    WsErrorCode,
    client_message_adapter,
)
from pitch_coach_backend.realtime.event_ingestion import InvalidAudioFrame
from pitch_coach_backend.realtime.storage.store import TranscriptStore
from pitch_coach_backend.realtime.stream import registry
from pitch_coach_backend.realtime.stream.auto_end import AutoEndPolicy
from pitch_coach_backend.realtime.stream.registry import StreamOwnerMismatch, TakeEnded
from pitch_coach_backend.realtime.stream.runtime import CoachDeps, TakeStream
from pitch_coach_backend.realtime.stt.adapter import SttAdapter, SttConfig
from pitch_coach_backend.realtime.stt.fillers import KEYTERM_FILLERS
from pitch_coach_backend.realtime.stt.keyterms import build_keyterms, normalize_term

logger = logging.getLogger(__name__)

# RFC 6455 close code
CLOSE_NORMAL = 1000
# 브라우저가 창을 닫거나 새로고침하며 끊었다. 네트워크가 끊기면 닫힘 프레임이 없어 1006 이 된다
CLOSE_GOING_AWAY = 1001
CLOSE_POLICY_VIOLATION = 1008  # 인증·권한·프로토콜 위반
CLOSE_INTERNAL_ERROR = 1011  # 서버 쪽 일시 오류. FE 는 다시 붙는다

AUTH_TIMEOUT_SEC = 5.0
# stop 뒤 스트림 정리를 기다리는 상한. 스트림이 Deepgram 을 기다리는 시간과 별개다 —
# FE 를 무한정 붙잡아 두지 않으려는 값이다
STOP_TIMEOUT_SEC = 12.0
# 정상 종료가 실패해 태스크를 끊은 뒤 정리를 기다리는 상한
CANCEL_TIMEOUT_SEC = 2.0

# 오디오를 받아도 되는 Take 상태 (FE `TakeStatus` 와 같은 값). 나머지(ANALYZING·COMPLETED·
# FAILED)는 이미 끝난 연습이라 전사를 더 붙이면 리포트가 오염된다.
# READY 는 첫 연결이다. 인가를 통과하면 RUNNING 으로 바꾼다 (`docs/ai/architecture.md` 1-3).
STREAMABLE_TAKE_STATUSES = frozenset({"READY", "RUNNING"})


def _origin_allowed(origin: str | None) -> bool:
    """Origin 이 있으면 프론트 주소와 같아야 한다. 없으면(스크립트 등 비브라우저) 통과.

    여기서는 Origin 이 실제 인증 수단이 아니라 추가 방어 수단이다. WS 핸드셰이크
    자체엔 인증 정보가 없고, 연결 뒤 첫 메시지로 Access Token 을 직접 보내야
    `RealtimeSession._authenticate` 를 통과한다 — 쿠키처럼 브라우저가 자동으로
    붙여주는 값이 아니므로, 공격 페이지가 Origin 을 속여 연결 자체는 만들어도
    토큰을 모르면 인증을 통과할 수 없다. 그래서 이 검사를 없애도 CSWSH 는 뚫리지
    않지만, 비용이 0 이라 방어선 하나로 남겨 둔다.

    Origin 이 없는 건 브라우저가 아니라는 뜻이다(서버 간 호출·스크립트 등은
    Origin 을 안 보낼 수 있다). 여기서 None 을 막으면 이런 정상적인 비브라우저
    호출까지 막게 되므로 통과시킨다.
    """
    if origin is None:
        return True
    try:
        given = urlsplit(origin)
        allowed = urlsplit(settings.frontend_base_url)
    except ValueError:
        return False
    return (given.scheme, given.netloc) == (allowed.scheme, allowed.netloc) and bool(given.netloc)


class RealtimeSession:
    def __init__(
        self,
        ws: WebSocket,
        *,
        take_id: uuid.UUID,
        db: Session,
        stt_adapter: SttAdapter,
        transcript_store: TranscriptStore,
        coach_client: CoachClient | None = None,
        coach_store: CoachStore | None = None,
    ) -> None:
        self.ws = ws
        self.take_id = take_id
        self.db = db
        self.stt_adapter = stt_adapter
        self.transcript_store = transcript_store
        self.coach = (
            CoachDeps(client=coach_client, store=coach_store)
            if coach_client is not None and coach_store is not None
            else None
        )

        # entity 가 아니라 id 만 들고 있는다. realtime 은 다른 도메인의 service 만 부른다
        self.user_id: uuid.UUID | None = None
        # Take 대본의 STT 용어 (우선순위 순, 자르기 전). 인가 때 같은 DB 왕복에서 읽는다
        self.script_terms: list[str] = []
        # 코치가 Take 동안 들고 있을 값. 인가 때 읽는다. 못 읽으면 코칭 없이 STT 만 돈다
        self.coach_context: TakeContext | None = None
        self.invalid_frames = 0
        # FE 가 끊을 때 보낸 닫힘 코드. 창을 닫았는지(1001) 보고 스트림이 얼마나 기다릴지 정한다
        self.close_code: int | None = None

    # ── 수명 ──────────────────────────────────────────────────────────

    async def run(self) -> None:
        if not _origin_allowed(self.ws.headers.get("origin")):
            await self.ws.close(code=CLOSE_POLICY_VIOLATION, reason="origin not allowed")
            return
        await self.ws.accept()

        user_id = await self._authenticate()
        if user_id is None:
            return
        if not await self._authorize(user_id):
            return
        self.user_id = user_id
        try:
            stream = await registry.attach(
                self.take_id,
                self.ws,
                owner_id=user_id,
                stt_adapter=self.stt_adapter,
                config=self._stt_config(),
                store=self.transcript_store,
                coach=self.coach,
                coach_context=self.coach_context,
            )
        except StreamOwnerMismatch:
            await self._reject(WsErrorCode.FORBIDDEN, "다른 사용자가 사용 중인 연습입니다.")
            return
        except TakeEnded as e:
            # 방금 끝낸 Take 다. DB 에 적히기 전이라 인가는 통과했다
            await self._reject_ended(e.reason)
            return

        # attach() 가 성공한 순간부터는 반드시 detach() 로 짝을 맞춘다. ready 전송이나
        # resend_missed 중간에 클라이언트가 사라져 예외가 나도 detach() 없이 빠져나가면
        # 스트림이 이 죽은 self.ws 를 "현재 클라이언트" 로 문 채 남는다 — grace 타이머가
        # 영영 안 걸려서 Deepgram 세션이 끝까지 살아 있게 된다
        try:
            # Deepgram 연결을 기다리지 않는다. 아직 붙기 전이면 stt_state 가 connecting 이고
            # 오디오는 큐에 쌓인다. 상태가 바뀌면 stt_status 가 뒤따른다
            await self._send(
                ReadyMessage(
                    take_id=self.take_id,
                    stt_session_no=stream.stt_session_no,
                    stt_state=stream.state,
                )
            )
            # 떨어져 있는 동안 화면에 못 간 final 을 먼저 따라잡는다
            await stream.resend_missed()
            await self._pump_client(stream)
        except WebSocketDisconnect:
            # ready 나 재전송 도중 클라이언트가 사라졌다 (Starlette 는 끊긴 상대에게 보내면
            # 이 예외를 던진다). 오류가 아니라 흔한 종료라 여기서 삼킨다 — 밖으로 새면 uvicorn
            # 이 연결마다 "Exception in ASGI application" 트레이스를 남긴다
            logger.info("ready/재전송 중 클라이언트가 끊겼다 take=%s", self.take_id)
        finally:
            stream.detach(self.ws, tab_closed=self.close_code == CLOSE_GOING_AWAY)

    async def _authenticate(self) -> uuid.UUID | None:
        """첫 메시지가 5초 안에 auth 여야 한다. 아니면 1008 로 닫는다. DB 는 안 본다.

        왜 첫 메시지인가 — 브라우저 `WebSocket` 생성자는 URL 과 서브프로토콜만 받고
        **헤더를 붙일 수 없다.** REST 처럼 `Authorization: Bearer` 를 실을 방법이 없고,
        Access 토큰은 XSS 노출을 피하려고 메모리에만 두므로 쿠키로도 못 보낸다.
        남는 건 URL 쿼리(토큰이 접속 로그·히스토리에 남는다) 아니면 연결 뒤 메시지다.
        그래서 핸드셰이크는 그냥 받고(`accept()`), 검증 전에는 오디오를 받지 않는다.
        """
        try:
            with anyio.fail_after(AUTH_TIMEOUT_SEC):
                message = await self.ws.receive()
        except TimeoutError:
            await self._reject(WsErrorCode.UNAUTHORIZED, "인증 메시지가 오지 않았습니다.")
            return None
        if message["type"] == "websocket.disconnect":
            return None

        text = message.get("text")
        if text is None:
            await self._reject(WsErrorCode.UNAUTHORIZED, "첫 메시지는 auth 여야 합니다.")
            return None
        try:
            parsed = client_message_adapter.validate_json(text)
        except ValidationError:
            await self._reject(WsErrorCode.UNAUTHORIZED, "첫 메시지는 auth 여야 합니다.")
            return None
        if not isinstance(parsed, AuthMessage):
            await self._reject(WsErrorCode.UNAUTHORIZED, "첫 메시지는 auth 여야 합니다.")
            return None

        try:
            payload = decode_access_token(parsed.token)
            return uuid.UUID(payload["sub"])
        except UnauthorizedException, ValueError, TypeError:
            await self._reject(WsErrorCode.UNAUTHORIZED, "유효하지 않은 토큰입니다.")
            return None

    async def _authorize(self, user_id: uuid.UUID) -> bool:
        """사용자 존재 · Take 존재·소유 · Take 상태를 DB 왕복 한 번으로 검사한다.

        통과하면 keyterm 에 쓸 대본 용어도 같이 읽어 둔다 (`self.script_terms`).

        없거나 남의 Take 는 `TAKE_NOT_FOUND` 하나로 답한다 (REST 의 `get_owned_pitch` 와
        같은 규칙 — 존재 여부를 흘리지 않는다). 끝난 Take 는 `TAKE_ENDED`. BE 가 자동으로 끝낸
        Take 는 상태가 `ANALYZING` 이라 같이 걸리고, 이유(`end_reason`)는 알림에만 쓴다.
        `READY` 면 `RUNNING` 으로 바꾼다. 다시 붙은 연결(이미 `RUNNING`)은 그대로 둔다.
        바꾸지 못하면(DB 오류) 1011 로 닫는다.
        """

        # 다시 붙은 연결이면 코치는 이미 Take 를 시작할 때의 값을 들고 있다
        need_context = self.coach is not None and not registry.coach_enabled(self.take_id)

        def load() -> tuple[
            bool, bool, bool, str | None, str | None, list[str], TakeContext | None
        ]:
            # 동기 SQLAlchemy 를 이벤트 루프에서 직접 부르면 다른 Take 의 오디오까지 멈춘다.
            # 그래서 threadpool 이고, 왕복을 아끼려 한 함수에서 전부 읽는다
            user = user_service.find(self.db, user_id)
            take = (
                take_service.find_owned(self.db, self.take_id, user_id)
                if user is not None
                else None
            )
            # rollback() 전에 값을 읽어 둔다. rollback() 은 세션의 객체를 전부 expire 시켜서
            # 그 뒤에 take.status 를 읽으면 SQLAlchemy 가 **커넥션을 다시 꺼내 SELECT 를
            # 한 번 더** 날린다 — 커넥션을 돌려준 의미가 없어진다. user 도 같은 이유로
            # id 를 안 읽고 이미 아는 user_id 를 그대로 쓴다
            status = take.status if take is not None else None
            end_reason = take.end_reason if take is not None else None
            terms: list[str] = []
            if status in STREAMABLE_TAKE_STATUSES:
                # 용어는 부가 기능이다. 조회가 실패해도 filler 만으로 STT 는 돈다 — 여기서
                # 예외를 흘리면 연결이 1011 로 닫히고 FE 재연결이 같은 실패를 되풀이한다.
                # 실패한 트랜잭션은 아래 rollback() 이 정리한다
                try:
                    terms = script_service.stt_keyterm_candidates(
                        self.db, take.pitch_id, take.script_version_id
                    )
                except Exception:
                    logger.exception("대본 용어 조회 실패, filler 만 쓴다 take=%s", self.take_id)
            # 조회가 끝나면 커넥션을 풀에 돌려준다. 이 연결은 몇 분씩 살아 있는데
            # 트랜잭션을 연 채로 두면 Take 수만큼 풀이 마른다
            self.db.rollback()
            take_found = take is not None
            started = True
            if status == "READY":
                # 첫 연결이 Take 를 시작한다. 위 조회와 따로 짧은 트랜잭션 하나로 쓴다 — 용어 조회가
                # 실패해 트랜잭션이 깨졌어도 적히고, 커밋하면 커넥션도 돌려준다.
                # 미션 · 평가 기준 버전 · 원자료 사용 동의가 고정되기 전에는 ready 를 보내지 않는다
                # (`docs/ai/architecture.md` 1-3 1번). 못 적으면 연결을 받지 않는다.
                # READY 로 남은 채 발표하면 BE 가 시작하지 않은 Take 로 끝내고
                # (`abandon_stale_ready_takes`) 끝내기도 받지 않는다. FE 는 ready 를 받기 전이라
                # 발표를 시작하지 않았고 다시 붙는다
                for attempt in range(2):
                    try:
                        if not lifecycle_service.mark_running(self.db, self.take_id):
                            # 그사이 다른 연결이 시작했거나 시작하지 않은 Take 로 끝났다.
                            # 다시 읽는다
                            again = take_service.find_owned(self.db, self.take_id, user_id)
                            take_found = again is not None
                            status = again.status if again is not None else None
                            end_reason = again.end_reason if again is not None else None
                            self.db.rollback()
                        break
                    except Exception as error:
                        self.db.rollback()
                        if attempt == 0 and is_transient_db_error(error):
                            logger.warning("Take 시작 값 저장을 다시 한다 take=%s", self.take_id)
                            continue
                        logger.exception("Take 를 RUNNING 으로 못 바꿨다 take=%s", self.take_id)
                        started = False
                        break
            context = None
            if started and status in STREAMABLE_TAKE_STATUSES and need_context:
                # 코칭은 부가 기능이다. 못 읽어도 STT 는 돈다
                try:
                    context = coach_store.load_take_context(self.db, self.take_id)
                except Exception:
                    logger.exception("코치 값을 읽지 못해 코칭 없이 돈다 take=%s", self.take_id)
                self.db.rollback()
            return user is not None, take_found, started, status, end_reason, terms, context

        (
            user_found,
            take_found,
            started,
            status,
            end_reason,
            terms,
            context,
        ) = await run_in_threadpool(load)
        if not user_found:
            await self._reject(WsErrorCode.UNAUTHORIZED, "유효하지 않은 토큰입니다.")
            return False
        if not take_found:
            await self._reject(WsErrorCode.TAKE_NOT_FOUND, "연습을 찾을 수 없습니다.")
            return False
        if not started:
            # 잠깐 DB 가 안 됐다. 1011 이면 FE 가 백오프로 다시 붙는다 (1000 · 1008 만 포기한다)
            await self._quiet(self.ws.close(code=CLOSE_INTERNAL_ERROR, reason="take not started"))
            return False
        if status not in STREAMABLE_TAKE_STATUSES:
            await self._reject_ended(end_reason)
            return False
        self.script_terms = terms
        self.coach_context = context
        return True

    def _stt_config(self) -> SttConfig:
        # filler 가 먼저다 — 군더더기 지표가 filler boosting 에 달려 있다. 대본 용어는 남는 자리에
        keyterms = build_keyterms(KEYTERM_FILLERS, self.script_terms)
        dropped = len({normalize_term(t) for t in self.script_terms} - {""} - set(keyterms))
        if dropped:
            logger.info(
                "keyterm 한도로 대본 용어를 뺐다 take=%s kept=%d dropped=%d",
                self.take_id,
                len(keyterms),
                dropped,
            )
        # 토큰 추정이 빗나가 Deepgram 이 거절하면 뒤쪽 대본 용어부터 줄인다. filler 는 남긴다
        return SttConfig(
            keyterms=keyterms,
            min_keyterms=len(build_keyterms(KEYTERM_FILLERS)),
            tag=f"take:{self.take_id}",
        )

    # ── 펌프 ──────────────────────────────────────────────────────────

    async def _pump_client(self, stream: TakeStream) -> None:
        while True:
            try:
                message = await self.ws.receive()
            except WebSocketDisconnect as e:
                self.close_code = e.code
                return
            if message["type"] == "websocket.disconnect":
                self.close_code = message.get("code")
                return

            data = message.get("bytes")
            if data is not None:
                await self._push_audio(stream, data)
                continue

            text = message.get("text")
            if text is None:
                continue
            try:
                parsed = client_message_adapter.validate_json(text)
            except ValidationError:
                await self._send(
                    ErrorMessage(code=WsErrorCode.BAD_MESSAGE, message="알 수 없는 메시지입니다.")
                )
                continue
            if isinstance(parsed, MetricsMessage):
                stream.push_metrics(parsed, source=self.ws)
                continue
            if isinstance(parsed, SlideMessage):
                stream.push_slide(parsed, source=self.ws)
                continue
            if isinstance(parsed, StopMessage):
                if not stream.is_client(self.ws):
                    # 다른 탭이 이어받아 쫓겨난 연결이 닫히기 전에 보냈다. 받으면 스트림이 멈추고
                    # 이어받은 탭의 오디오는 멈춘 스트림에 알림 없이 버려진다 (오디오와 같은 규칙)
                    logger.info("쫓겨난 연결의 stop 을 무시한다 take=%s", self.take_id)
                    return
                await self._stop(stream)
                return
            # auth 가 또 오면 무시한다. 이미 인증됐다

    async def _push_audio(self, stream: TakeStream, data: bytes) -> None:
        try:
            stream.push(data, source=self.ws)
        except InvalidAudioFrame as e:
            self.invalid_frames += 1
            if self.invalid_frames == 1:
                # 한 번만 알린다. 매 프레임 알리면 잘못된 클라이언트가 우리 큐를 채운다
                await self._send(ErrorMessage(code=WsErrorCode.BAD_AUDIO_FRAME, message=str(e)))

    async def _stop(self, stream: TakeStream) -> None:
        """큐에 남은 오디오까지 보내고 Deepgram 을 정리한 뒤 Take 를 끝내고 닫는다.

        FE 는 stt_status=closed 를 받기 전에 연결을 닫지 않는다 — 먼저 닫으면
        마지막 1~2초 전사가 사라진다.
        """
        # 이 순간부터 같은 Take 로 새로 붙는 연결은 거절된다. DB 는 아래에서 적는다
        stream.end()
        if not await stream.wait_closed(STOP_TIMEOUT_SEC):
            # Deepgram 이 CloseStream 에 응답하지 않았다. 그냥 잊으면 스트림은 계속 돌면서
            # 레지스트리에서만 사라진다 — 태스크를 끊고 소켓을 닫은 뒤에 보낸다
            logger.warning("Deepgram drain 타임아웃 take=%s. 스트림을 끊는다", self.take_id)
            stream.cancel()
            # 취소마저 제때 안 끝나면 (abort 가 ABORT_TIMEOUT_SEC 까지 걸리는 경우) 스트림
            # 쪽에서 닫힌 것으로 확정한다. FE 를 더 붙잡아 둘 이유가 없다 — final 은 이미
            # 저장됐고, 남은 소켓 정리는 reaper 가 마저 한다
            await stream.wait_cancelled(CANCEL_TIMEOUT_SEC)
        # 마지막 확정 전사와 1초 원자료를 저장한 뒤에 끝난 것으로 적는다. Take 뒤 분석은 ANALYZING
        # 을 보고 시작하므로 그때 원자료가 다 있어야 한다. FE 는 마지막 1초 조각을 stop 앞에 보낸다.
        # FE 가 `auto_end` 를 받고 보낸 stop 이면 자동 종료 이유를 같이 적는다 — 자동 종료 정리와
        # 누가 먼저 적든 이유가 남는다. 못 적으면 뒤에서 다시 적고, 그동안 이 프로세스 안에서는
        # `TakeStream.end` 가 다시 붙는 연결을 막는다
        reason = stream.auto_end_reason
        await registry.finish_take(
            self.take_id,
            self.transcript_store,
            end_reason=reason.value if reason is not None else None,
            coach=self.coach,
        )
        registry.forget(stream)
        # FE 가 그 사이 끊겼어도(탭 닫힘) 아래 둘은 조용히 실패하고, 스트림 정리와 저장은
        # 이미 끝났다 — FE 가 있든 없든 서버 쪽 종료 절차는 같다
        await stream.send_status()
        await self._quiet(self.ws.close(code=CLOSE_NORMAL))

    # ── 보조 ──────────────────────────────────────────────────────────

    async def _send(self, message: BaseModel) -> None:
        await self.ws.send_text(message.model_dump_json())

    async def _reject(
        self, code: WsErrorCode, message: str, *, end_reason: TakeEndReason | None = None
    ) -> None:
        """인증 단계 거절. 에러를 한 번 보내고 1008 로 닫는다."""
        await self._quiet(
            self._send(ErrorMessage(code=code, message=message, end_reason=end_reason))
        )
        await self._quiet(self.ws.close(code=CLOSE_POLICY_VIOLATION, reason=code))

    async def _reject_ended(self, end_reason: str | None) -> None:
        """끝난 Take 라 `TAKE_ENDED` 로 거절한다.

        BE 가 자동으로 끝낸 Take 면 그 앞에 `auto_end` 를 한 번 더 보낸다. 자동 종료 순간 떨어져
        있던 FE 는 `auto_end` 를 못 받았다 — 다시 붙을 때 받아야 무대가 종료 흐름을 탄다. 이유가
        이 버전이 모르는 값(나중에 늘어남)이면 FE 도 분기할 수 없으니 일반 문구만 보낸다.

        `TAKE_ENDED` 에는 BE 가 끝낸 이유(`end_reason`)를 싣는다. FE 는 떠난 것으로 끝낸 Take 중
        창을 닫은 것(`CLOSED`)과 STT 없이 발표를 이어 가도 되는 것(`ABANDONED`)을 이걸로 가른다.
        """
        known = cast(TakeEndReason, end_reason) if end_reason in TAKE_END_REASONS else None
        try:
            reason = AutoEndReason(end_reason) if end_reason is not None else None
        except ValueError:
            reason = None
        if reason is None:
            await self._reject(WsErrorCode.TAKE_ENDED, "이미 끝난 연습입니다.", end_reason=known)
            return
        message = AutoEndPolicy.from_settings().message(reason)
        await self._quiet(self._send(AutoEndMessage(reason=reason, message=message)))
        await self._reject(WsErrorCode.TAKE_ENDED, message, end_reason=known)

    @staticmethod
    async def _quiet(awaitable: Awaitable[object]) -> None:
        """이미 끊긴 상대에게 보내거나 닫을 때 나는 예외는 무시한다."""
        with contextlib.suppress(WebSocketDisconnect, RuntimeError, OSError):
            await awaitable
