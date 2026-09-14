"""WebSocket 연결 하나의 수명을 책임진다: 인증 -> Deepgram 연결 -> 양방향 중계 -> 정리.

    FE ──(WS-1)── BE ──(WS-2)── Deepgram

연결마다 태스크 셋이 돈다.
- client pump : FE 프레임을 검증해 Deepgram 으로. `stop` 이 오면 CloseStream.
- stt pump    : Deepgram 이벤트를 Take 기준 타임스탬프로 바꿔 FE 로.
- keepalive   : 오디오가 멈춘 동안 Deepgram 이 10초 타임아웃으로 끊지 않게.

타임스탬프 규칙
    take_ms = base_offset_ms + deepgram_ms
    base_offset_ms = 이 Deepgram 세션에 처음 보낸 프레임의 offset_ms
갭은 무음으로 채우므로(event_ingestion) 한 세션 안에서 base 는 바뀌지 않는다.

아직 없는 것 (다음 PR): final 세그먼트 저장, Take 소유권·RUNNING 검사, WS-1/WS-2 재연결.
"""

import contextlib
import logging
import uuid
from collections.abc import Awaitable
from typing import Literal
from urllib.parse import urlsplit

import anyio
from fastapi import WebSocket, WebSocketDisconnect
from pydantic import BaseModel, ValidationError
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool
from websockets.exceptions import ConnectionClosed

from pitch_coach_backend.core.config import settings
from pitch_coach_backend.core.exceptions import UnauthorizedException
from pitch_coach_backend.core.security import decode_access_token
from pitch_coach_backend.module.user import service as user_service
from pitch_coach_backend.module.user.entity import User
from pitch_coach_backend.realtime.audio import silence
from pitch_coach_backend.realtime.dto import (
    ErrorMessage,
    ReadyMessage,
    StopMessage,
    SttStatusMessage,
    TranscriptMessage,
    WordOut,
    client_message_adapter,
)
from pitch_coach_backend.realtime.event_ingestion import (
    FrameSequencer,
    InvalidAudioFrame,
    parse_audio_frame,
)
from pitch_coach_backend.realtime.fillers import KEYTERM_FILLERS
from pitch_coach_backend.realtime.stt_adapter import (
    Metadata,
    SttAdapter,
    SttConfig,
    SttConnectError,
    SttError,
    SttSession,
    Transcript,
)

logger = logging.getLogger(__name__)

# RFC 6455 close code
CLOSE_NORMAL = 1000
CLOSE_POLICY_VIOLATION = 1008  # 인증·권한·프로토콜 위반
CLOSE_INTERNAL_ERROR = 1011  # 우리 쪽 또는 Deepgram 쪽 장애

AUTH_TIMEOUT_SEC = 5.0
# CloseStream 뒤 Deepgram 이 남은 결과와 Metadata 를 주고 닫을 때까지 기다리는 상한
DRAIN_TIMEOUT_SEC = 10.0

ClientOutcome = Literal["stop", "disconnect", "stt_closed", "error"]
SttState = Literal["ok", "degraded", "reconnecting", "closed"]


def _origin_allowed(origin: str | None) -> bool:
    """Origin 이 있으면 프론트 주소와 같아야 한다. 없으면(스크립트 등 비브라우저) 통과.

    쿠키가 아니라 토큰으로 인증하므로 CSWSH 위험은 낮지만 검사 비용이 0 이다.
    auth/dependencies 의 출처 비교와 같은 단위(스킴+호스트+포트) 로 본다.
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
    ) -> None:
        self.ws = ws
        self.take_id = take_id
        self.db = db
        self.stt_adapter = stt_adapter

        self.user: User | None = None
        self.stt_session_no = 0
        # final 마다 1 씩 오른다. interim 은 다음 final 이 올 때까지 같은 번호를 쓴다
        self.segment_no = 1
        self.base_offset_ms: int | None = None
        self.sequencer = FrameSequencer()
        self.invalid_frames = 0
        self.stt_metadata: Metadata | None = None

    # ── 수명 ──────────────────────────────────────────────────────────

    async def run(self) -> None:
        if not _origin_allowed(self.ws.headers.get("origin")):
            await self.ws.close(code=CLOSE_POLICY_VIOLATION, reason="origin not allowed")
            return
        await self.ws.accept()

        self.user = await self._authenticate()
        if self.user is None:
            return
        # 다음 PR: Take 소유권 + RUNNING 검사가 여기 들어간다 (take 모듈 필요)

        try:
            stt = await self.stt_adapter.connect(self._stt_config())
        except SttConnectError:
            logger.exception("Deepgram 연결 실패 take=%s", self.take_id)
            await self._fail("STT_UNAVAILABLE", "음성 인식 서버에 연결할 수 없습니다.")
            return
        self.stt_session_no += 1

        await self._send(ReadyMessage(take_id=self.take_id, stt_session_no=self.stt_session_no))
        await self._stream(stt)

    async def _authenticate(self) -> User | None:
        """첫 메시지가 5초 안에 auth 여야 한다. 아니면 1008 로 닫는다."""
        try:
            with anyio.fail_after(AUTH_TIMEOUT_SEC):
                message = await self.ws.receive()
        except TimeoutError:
            await self._reject("UNAUTHORIZED", "인증 메시지가 오지 않았습니다.")
            return None
        if message["type"] == "websocket.disconnect":
            return None

        text = message.get("text")
        if text is None:
            await self._reject("UNAUTHORIZED", "첫 메시지는 auth 여야 합니다.")
            return None
        try:
            parsed = client_message_adapter.validate_json(text)
        except ValidationError:
            await self._reject("UNAUTHORIZED", "첫 메시지는 auth 여야 합니다.")
            return None
        if isinstance(parsed, StopMessage):
            await self._reject("UNAUTHORIZED", "첫 메시지는 auth 여야 합니다.")
            return None

        try:
            payload = decode_access_token(parsed.token)
            user_id = uuid.UUID(payload["sub"])
        except UnauthorizedException, ValueError, TypeError:
            await self._reject("UNAUTHORIZED", "유효하지 않은 토큰입니다.")
            return None

        # 동기 SQLAlchemy 를 이벤트 루프에서 직접 부르면 다른 Take 의 오디오까지 멈춘다
        user = await run_in_threadpool(user_service.find, self.db, user_id)
        # 조회가 끝나면 커넥션을 풀에 돌려준다. 이 연결은 몇 분씩 살아 있는데
        # 트랜잭션을 연 채로 두면 Take 수만큼 풀이 마른다
        await run_in_threadpool(self.db.rollback)
        if user is None:
            await self._reject("UNAUTHORIZED", "유효하지 않은 토큰입니다.")
            return None
        return user

    def _stt_config(self) -> SttConfig:
        # 다음 PR: Pitch 대본 키워드를 filler 뒤에 붙인다 (keyterm 100개·500토큰 상한)
        return SttConfig(keyterms=KEYTERM_FILLERS, tag=f"take:{self.take_id}")

    async def _stream(self, stt: SttSession) -> None:
        """펌프 셋을 한 task group 에서 돌린다.

        asyncio.wait/gather 로 직접 짜면 Starlette 의 anyio 취소 스코프와 어긋나
        연결 종료 시 CancelledError 가 새어 나온다. Starlette 가 쓰는 도구를 그대로 쓴다.
        """
        outcome: ClientOutcome | None = None
        stt_finished = anyio.Event()

        async def client_pump() -> None:
            nonlocal outcome
            try:
                outcome = await self._pump_client(stt)
            except Exception:
                logger.exception("client pump 실패 take=%s", self.take_id)
                outcome = "error"
            await self._finish_by_client(outcome, stt_finished)
            tg.cancel_scope.cancel()

        async def stt_pump() -> None:
            try:
                await self._pump_stt(stt)
            except Exception:
                logger.exception("stt pump 실패 take=%s", self.take_id)
            finally:
                stt_finished.set()
            if outcome is None:
                # Deepgram 이 먼저 끊겼다. 다음 PR 에서는 여기서 재연결한다
                await self._fail("STT_UNAVAILABLE", "음성 인식 연결이 끊겼습니다.")
                tg.cancel_scope.cancel()

        try:
            async with anyio.create_task_group() as tg:
                tg.start_soon(client_pump)
                tg.start_soon(stt_pump)
                tg.start_soon(stt.keepalive_loop)
        finally:
            await self._quiet(stt.abort())

    async def _finish_by_client(self, outcome: ClientOutcome, stt_finished: anyio.Event) -> None:
        match outcome:
            case "stop":
                # CloseStream 은 pump 안에서 이미 보냈다. Metadata 까지 받고 닫는다
                with anyio.move_on_after(DRAIN_TIMEOUT_SEC) as scope:
                    await stt_finished.wait()
                if scope.cancelled_caught:
                    logger.warning("Deepgram drain 타임아웃 take=%s", self.take_id)
                await self._quiet(self._send(self._status("closed")))
                await self._quiet(self.ws.close(code=CLOSE_NORMAL))
            case "disconnect":
                # FE 가 먼저 끊겼다. 다음 PR 에서는 30초 동안 세션을 살려 두고 재연결을 받는다
                logger.info("FE 연결 끊김 take=%s frames=%d", self.take_id, self.sequencer.frames)
            case "stt_closed":
                await self._fail("STT_UNAVAILABLE", "음성 인식 연결이 끊겼습니다.")
            case "error":
                await self._fail("INTERNAL_SERVER_ERROR", "처리 중 오류가 발생했습니다.")

    # ── 펌프 ──────────────────────────────────────────────────────────

    async def _pump_client(self, stt: SttSession) -> ClientOutcome:
        while True:
            try:
                message = await self.ws.receive()
            except WebSocketDisconnect:
                return "disconnect"
            if message["type"] == "websocket.disconnect":
                return "disconnect"

            data = message.get("bytes")
            if data is not None:
                try:
                    await self._forward_audio(stt, data)
                except ConnectionClosed:
                    return "stt_closed"
                continue

            text = message.get("text")
            if text is None:
                continue
            try:
                parsed = client_message_adapter.validate_json(text)
            except ValidationError:
                await self._send(
                    ErrorMessage(code="BAD_MESSAGE", message="알 수 없는 메시지입니다.")
                )
                continue
            if isinstance(parsed, StopMessage):
                try:
                    await stt.close_stream()
                except ConnectionClosed:
                    return "stt_closed"
                return "stop"
            # auth 가 또 오면 무시한다. 이미 인증됐다

    async def _forward_audio(self, stt: SttSession, data: bytes) -> None:
        try:
            frame = parse_audio_frame(data)
        except InvalidAudioFrame as e:
            self.invalid_frames += 1
            if self.invalid_frames == 1:
                # 한 번만 알린다. 매 프레임 알리면 잘못된 클라이언트가 우리 큐를 채운다
                await self._send(ErrorMessage(code="BAD_AUDIO_FRAME", message=str(e)))
            return

        accepted = self.sequencer.accept(frame)
        if accepted is None:
            return
        if accepted.silence_ms:
            await stt.send_audio(silence(accepted.silence_ms))
        if accepted.lost_ms:
            logger.warning("오디오 유실 take=%s lost_ms=%d", self.take_id, accepted.lost_ms)
        if self.base_offset_ms is None:
            self.base_offset_ms = frame.offset_ms
        await stt.send_audio(frame.pcm)

    async def _pump_stt(self, stt: SttSession) -> None:
        async for event in stt:
            match event:
                case Transcript():
                    await self._on_transcript(event)
                case Metadata():
                    self.stt_metadata = event
                    logger.info(
                        "Deepgram 세션 종료 take=%s request_id=%s duration_ms=%d",
                        self.take_id,
                        event.request_id,
                        event.duration_ms,
                    )
                case SttError():
                    logger.warning(
                        "Deepgram 오류 take=%s %s %s", self.take_id, event.code, event.message
                    )
                case _:
                    # UtteranceEnd / SpeechStarted 는 아직 쓰지 않는다
                    pass

    async def _on_transcript(self, event: Transcript) -> None:
        base = self.base_offset_ms or 0
        if event.transcript or event.words:
            await self._send(
                TranscriptMessage(
                    segment_id=f"{self.stt_session_no}-{self.segment_no}",
                    is_final=event.is_final,
                    speech_final=event.speech_final,
                    start_ms=base + event.start_ms,
                    end_ms=base + event.end_ms,
                    text=event.transcript,
                    confidence=event.confidence,
                    words=[
                        WordOut(
                            word=w.word,
                            punctuated_word=w.punctuated_word,
                            start_ms=base + w.start_ms,
                            end_ms=base + w.end_ms,
                            confidence=w.confidence,
                        )
                        for w in event.words
                    ],
                )
            )
        if event.is_final:
            # 빈 final 도 구간을 닫는다. 다음 interim 은 새 번호로
            self.segment_no += 1

    # ── 보조 ──────────────────────────────────────────────────────────

    def _status(self, state: SttState) -> SttStatusMessage:
        return SttStatusMessage(
            state=state,
            stt_session_no=self.stt_session_no,
            frames=self.sequencer.frames,
            dropped_frames=self.sequencer.dropped,
            silence_ms=self.sequencer.silence_ms,
            lost_ms=self.sequencer.lost_ms,
        )

    async def _send(self, message: BaseModel) -> None:
        await self.ws.send_text(message.model_dump_json())

    async def _reject(self, code: str, message: str) -> None:
        """인증 단계 거절. 에러를 한 번 보내고 1008 로 닫는다."""
        await self._quiet(self._send(ErrorMessage(code=code, message=message)))
        await self._quiet(self.ws.close(code=CLOSE_POLICY_VIOLATION, reason=code))

    async def _fail(self, code: str, message: str) -> None:
        await self._quiet(self._send(ErrorMessage(code=code, message=message)))
        await self._quiet(self.ws.close(code=CLOSE_INTERNAL_ERROR, reason=code))

    @staticmethod
    async def _quiet(awaitable: Awaitable[object]) -> None:
        """이미 끊긴 상대에게 보내거나 닫을 때 나는 예외는 무시한다."""
        with contextlib.suppress(WebSocketDisconnect, ConnectionClosed, RuntimeError, OSError):
            await awaitable
