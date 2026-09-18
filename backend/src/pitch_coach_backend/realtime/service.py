"""WebSocket 연결 하나를 인증하고 Take 의 STT 스트림에 붙인다.

    FE ──(WS-1)── RealtimeSession ──push──▶ TakeStream ──(WS-2)── Deepgram

이 클래스는 **연결만큼만** 산다. 오디오 파이프라인과 Deepgram 세션은 `take_stream.TakeStream`
이 들고 있고 연결보다 오래 남는다 — 탭을 새로 고쳐도 세그먼트 번호와 타임라인이 이어지도록.

연결 하나의 순서: Origin → auth(JWT) → 인가(DB 1회: 사용자·Take 존재·소유·상태) → ready → 오디오.
"""

import contextlib
import logging
import uuid
from collections.abc import Awaitable
from urllib.parse import urlsplit

import anyio
from fastapi import WebSocket, WebSocketDisconnect
from pydantic import BaseModel, ValidationError
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from pitch_coach_backend.core.config import settings
from pitch_coach_backend.core.exceptions import UnauthorizedException
from pitch_coach_backend.core.security import decode_access_token
from pitch_coach_backend.module.take import service as take_service
from pitch_coach_backend.module.user import service as user_service
from pitch_coach_backend.realtime import take_stream
from pitch_coach_backend.realtime.dto import (
    ErrorMessage,
    ReadyMessage,
    StopMessage,
    WsErrorCode,
    client_message_adapter,
)
from pitch_coach_backend.realtime.event_ingestion import InvalidAudioFrame
from pitch_coach_backend.realtime.fillers import KEYTERM_FILLERS
from pitch_coach_backend.realtime.stt_adapter import SttAdapter, SttConfig
from pitch_coach_backend.realtime.take_stream import StreamOwnerMismatch, TakeStream
from pitch_coach_backend.realtime.transcript_store import TranscriptStore

logger = logging.getLogger(__name__)

# RFC 6455 close code
CLOSE_NORMAL = 1000
CLOSE_POLICY_VIOLATION = 1008  # 인증·권한·프로토콜 위반

AUTH_TIMEOUT_SEC = 5.0
# stop 뒤 스트림 정리를 기다리는 상한. 스트림이 Deepgram 을 기다리는 시간과 별개다 —
# FE 를 무한정 붙잡아 두지 않으려는 값이다
STOP_TIMEOUT_SEC = 12.0
# 정상 종료가 실패해 태스크를 끊은 뒤 정리를 기다리는 상한
CANCEL_TIMEOUT_SEC = 2.0

# 오디오를 받아도 되는 Take 상태 (FE `TakeStatus` 와 같은 값). 나머지(ANALYZING·COMPLETED·
# FAILED)는 이미 끝난 연습이라 전사를 더 붙이면 리포트가 오염된다.
# READY 도 받는다 — READY→RUNNING 전이는 take 모듈(FE 의 started_at 갱신)이 맡고, WebSocket 이
# 열리는 시점과 그 전이 시점의 순서를 FE 에 강제하지 않기 위해서다.
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
    ) -> None:
        self.ws = ws
        self.take_id = take_id
        self.db = db
        self.stt_adapter = stt_adapter
        self.transcript_store = transcript_store

        # entity 가 아니라 id 만 들고 있는다. realtime 은 다른 도메인의 service 만 부른다
        self.user_id: uuid.UUID | None = None
        self.invalid_frames = 0

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
            stream = await take_stream.attach(
                self.take_id,
                self.ws,
                owner_id=user_id,
                stt_adapter=self.stt_adapter,
                config=self._stt_config(),
                store=self.transcript_store,
            )
        except StreamOwnerMismatch:
            await self._reject(WsErrorCode.FORBIDDEN, "다른 사용자가 사용 중인 연습입니다.")
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
        finally:
            stream.detach(self.ws)

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
        if isinstance(parsed, StopMessage):
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

        없거나 남의 Take 는 `TAKE_NOT_FOUND` 하나로 답한다 (REST 의 `get_owned_pitch` 와
        같은 규칙 — 존재 여부를 흘리지 않는다). 끝난 Take 는 `TAKE_ENDED`.
        """

        def load() -> tuple[bool, bool, str | None]:
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
            # 조회가 끝나면 커넥션을 풀에 돌려준다. 이 연결은 몇 분씩 살아 있는데
            # 트랜잭션을 연 채로 두면 Take 수만큼 풀이 마른다
            self.db.rollback()
            return user is not None, take is not None, status

        user_found, take_found, status = await run_in_threadpool(load)
        if not user_found:
            await self._reject(WsErrorCode.UNAUTHORIZED, "유효하지 않은 토큰입니다.")
            return False
        if not take_found:
            await self._reject(WsErrorCode.TAKE_NOT_FOUND, "연습을 찾을 수 없습니다.")
            return False
        if status not in STREAMABLE_TAKE_STATUSES:
            await self._reject(WsErrorCode.TAKE_ENDED, "이미 끝난 연습입니다.")
            return False
        return True

    def _stt_config(self) -> SttConfig:
        # 다음 PR: Pitch 대본 키워드를 filler 뒤에 붙인다 (keyterm 100개·500토큰 상한)
        return SttConfig(keyterms=KEYTERM_FILLERS, tag=f"take:{self.take_id}")

    # ── 펌프 ──────────────────────────────────────────────────────────

    async def _pump_client(self, stream: TakeStream) -> None:
        while True:
            try:
                message = await self.ws.receive()
            except WebSocketDisconnect:
                return
            if message["type"] == "websocket.disconnect":
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
            if isinstance(parsed, StopMessage):
                await self._stop(stream)
                return
            # auth 가 또 오면 무시한다. 이미 인증됐다

    async def _push_audio(self, stream: TakeStream, data: bytes) -> None:
        try:
            stream.push(data)
        except InvalidAudioFrame as e:
            self.invalid_frames += 1
            if self.invalid_frames == 1:
                # 한 번만 알린다. 매 프레임 알리면 잘못된 클라이언트가 우리 큐를 채운다
                await self._send(ErrorMessage(code=WsErrorCode.BAD_AUDIO_FRAME, message=str(e)))

    async def _stop(self, stream: TakeStream) -> None:
        """큐에 남은 오디오까지 보내고 Deepgram 을 정리한 뒤 닫는다.

        FE 는 stt_status=closed 를 받기 전에 연결을 닫지 않는다 — 먼저 닫으면
        마지막 1~2초 전사가 사라진다.
        """
        stream.request_stop()
        if not await stream.wait_closed(STOP_TIMEOUT_SEC):
            # Deepgram 이 CloseStream 에 응답하지 않았다. 그냥 잊으면 스트림은 계속 돌면서
            # 레지스트리에서만 사라진다 — 태스크를 끊고 소켓을 닫은 뒤에 보낸다
            logger.warning("Deepgram drain 타임아웃 take=%s. 스트림을 끊는다", self.take_id)
            stream.cancel()
            await stream.wait_closed(timeout=CANCEL_TIMEOUT_SEC)
        take_stream.forget(stream)
        await stream.send_status()
        await self._quiet(self.ws.close(code=CLOSE_NORMAL))

    # ── 보조 ──────────────────────────────────────────────────────────

    async def _send(self, message: BaseModel) -> None:
        await self.ws.send_text(message.model_dump_json())

    async def _reject(self, code: WsErrorCode, message: str) -> None:
        """인증 단계 거절. 에러를 한 번 보내고 1008 로 닫는다."""
        await self._quiet(self._send(ErrorMessage(code=code, message=message)))
        await self._quiet(self.ws.close(code=CLOSE_POLICY_VIOLATION, reason=code))

    @staticmethod
    async def _quiet(awaitable: Awaitable[object]) -> None:
        """이미 끊긴 상대에게 보내거나 닫을 때 나는 예외는 무시한다."""
        with contextlib.suppress(WebSocketDisconnect, RuntimeError, OSError):
            await awaitable
