"""WebSocket 연결 하나를 인증하고 Take 의 STT 스트림에 붙인다.

    FE ──(WS-1)── RealtimeSession ──push──▶ TakeStream ──(WS-2)── Deepgram

이 클래스는 **연결만큼만** 산다. 오디오 파이프라인과 Deepgram 세션은 `runtime.TakeStream`
이 들고 있고 연결보다 오래 남는다 — 탭을 새로고침해도 세그먼트 번호가 이어지도록.

연결 하나의 순서: 접속 허용(Origin → auth(JWT) → 인가, `handshake.py`) → 스트림에 붙이기
(`stream/registry.attach`) → ready → 오디오 · 1초 측정값(metrics) · 장 변경(slide) → stop(마지막
전사 · 측정값까지 저장한 뒤 ANALYZING · FINALIZING 으로 적고 Take 결과를 띄운다).

STT 재접속 · 전사 저장 재시도는 직접 하지 않는다 (스트림이 한다). 받은 프레임과 stop 을 넘길 뿐이다.
"""

import contextlib
import logging
import uuid
from collections.abc import Awaitable

from fastapi import WebSocket, WebSocketDisconnect
from pydantic import BaseModel, ValidationError
from sqlalchemy.orm import Session

from pitch_coach_backend.coach.client import CoachClient
from pitch_coach_backend.coach.requests import TakeContext
from pitch_coach_backend.coach.store import CoachStore
from pitch_coach_backend.realtime.connection.handshake import TOO_MANY_STREAMS_MESSAGE, Handshake
from pitch_coach_backend.realtime.dto import (
    ErrorMessage,
    MetricsMessage,
    ReadyMessage,
    SlideMessage,
    StopMessage,
    WsErrorCode,
    client_message_adapter,
)
from pitch_coach_backend.realtime.event_ingestion import InvalidAudioFrame
from pitch_coach_backend.realtime.storage.store import TranscriptStore
from pitch_coach_backend.realtime.stream import finish, registry
from pitch_coach_backend.realtime.stream.record import CoachDeps
from pitch_coach_backend.realtime.stream.registry import (
    StreamClosing,
    StreamOwnerMismatch,
    TakeEnded,
    TooManyStreams,
)
from pitch_coach_backend.realtime.stream.runtime import TakeStream
from pitch_coach_backend.realtime.stt.adapter import SttAdapter, SttConfig
from pitch_coach_backend.realtime.stt.fillers import KEYTERM_FILLERS
from pitch_coach_backend.realtime.stt.keyterms import build_keyterms, normalize_term

logger = logging.getLogger(__name__)

# RFC 6455 close code
CLOSE_NORMAL = 1000
# 브라우저가 창을 닫거나 새로고침하며 끊었다. 네트워크가 끊기면 닫힘 프레임이 없어 1006 이 된다
CLOSE_GOING_AWAY = 1001

# stop 뒤 스트림 정리를 기다리는 상한. 스트림이 Deepgram 을 기다리는 시간과 별개다 —
# FE 를 무한정 붙잡아 두지 않으려는 값이다
STOP_TIMEOUT_SEC = 12.0
# 정상 종료가 실패해 태스크를 끊은 뒤 정리를 기다리는 상한
CANCEL_TIMEOUT_SEC = 2.0


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
        handshake = Handshake(
            self.ws,
            take_id=self.take_id,
            db=self.db,
            needs_coach_context=lambda: (
                self.coach is not None and not registry.coach_enabled(self.take_id)
            ),
            has_room=lambda user_id: registry.has_room(user_id, self.take_id),
        )
        if not await handshake.admit():
            return
        assert handshake.user_id is not None  # admit() 를 통과했다
        user_id = handshake.user_id
        self.user_id = user_id
        self.script_terms = handshake.script_terms
        self.coach_context = handshake.coach_context
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
            await handshake.reject(WsErrorCode.FORBIDDEN, "다른 사용자가 사용 중인 연습입니다.")
            return
        except TakeEnded as e:
            # 방금 끝낸 Take 다. DB 에 적히기 전이라 인가는 통과했다
            await handshake.reject_ended(e.reason)
            return
        except TooManyStreams:
            await handshake.reject(WsErrorCode.TOO_MANY_STREAMS, TOO_MANY_STREAMS_MESSAGE)
            return
        except StreamClosing:
            # 이전 스트림 정리가 늦다. 곧 닫히므로 FE 가 다시 붙게 한다
            await handshake.reject_busy()
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
            await stream.resend_missed(source=self.ws)
            await self._pump_client(stream)
        except WebSocketDisconnect:
            # ready 나 재전송 도중 클라이언트가 사라졌다 (Starlette 는 끊긴 상대에게 보내면
            # 이 예외를 던진다). 오류가 아니라 흔한 종료라 여기서 삼킨다 — 밖으로 새면 uvicorn
            # 이 연결마다 "Exception in ASGI application" 트레이스를 남긴다
            logger.info("ready/재전송 중 클라이언트가 끊겼다 take=%s", self.take_id)
        finally:
            stream.detach(self.ws, tab_closed=self.close_code == CLOSE_GOING_AWAY)

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
        await finish.finish_take(
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

    @staticmethod
    async def _quiet(awaitable: Awaitable[object]) -> None:
        """이미 끊긴 상대에게 보내거나 닫을 때 나는 예외는 무시한다."""
        with contextlib.suppress(WebSocketDisconnect, RuntimeError, OSError):
            await awaitable
