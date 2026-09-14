"""한 Take 의 STT 파이프라인. WebSocket 연결보다 오래 산다.

    FE ──(WS-1)── RealtimeSession ──push──▶ TakeStream ──(WS-2)── Deepgram

연결(`RealtimeSession`)은 탭을 새로 고치면 바뀌지만 `TakeStream` 은 남는다. 그래서
세그먼트 번호·프레임 순서·Deepgram 세션 번호가 재연결 뒤에도 이어진다. 이 값들이 연결 객체
안에 있으면 재연결마다 1 로 리셋되고, `segment_id` 가 `"1-1"` 부터 다시 나와 FE 가
**발표 앞부분 전사를 뒷부분으로 덮어쓴다.**

두 가지 끊김을 따로 다룬다.

- **WS-1** (FE↔BE): 연결만 떨어진다. Deepgram 세션은 `GRACE_SEC` 동안 살려 두고 그 안에
  다시 붙으면 같은 세션에 이어 붙인다. 못 붙으면 `CloseStream` 으로 정리한다.
- **WS-2** (BE↔Deepgram): 큐에 담아 두고 백오프로 재접속한다. 새 세션은 타임스탬프가 다시
  0 부터라 `base_offset_ms` 를 새로 잡고 `stt_session_no` 를 올린다. 재접속이 이어서 실패해도
  **연결을 끊지 않는다** — FE 1단 코치는 계속 돌아야 하므로 `degraded` 만 알리고 재시도한다.

MVP 는 uvicorn 1 프로세스라 레지스트리가 in-process dict 다. 워커를 늘리려면 sticky 라우팅이나
Redis 가 필요한데 그때는 어차피 재설계다 (계획 문서 §8-6).
"""

import asyncio
import contextlib
import logging
import uuid
from collections.abc import Awaitable

import anyio
from fastapi import WebSocket
from pydantic import BaseModel
from websockets.exceptions import ConnectionClosed

from pitch_coach_backend.realtime.audio import BYTES_PER_MS, silence
from pitch_coach_backend.realtime.dto import (
    ErrorMessage,
    SttState,
    SttStatusMessage,
    TranscriptMessage,
    WordOut,
)
from pitch_coach_backend.realtime.event_ingestion import (
    Accepted,
    FrameSequencer,
    parse_audio_frame,
)
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

# FE 가 끊긴 뒤 Deepgram 세션을 살려 두는 시간. 탭 새로고침·와이파이 전환을 덮는다
GRACE_SEC = 30.0
# Deepgram 이 죽어 있는 동안 들고 있을 오디오. 100ms 프레임 기준 5초
QUEUE_MAX_FRAMES = 50
# 재접속 간격(초). 이 뒤로는 마지막 값을 반복한다
BACKOFF_SEC = (0.5, 1.0, 2.0, 5.0, 10.0)
# 이 횟수만큼 실패하면 FE 에 degraded 를 알린다. 재시도는 계속한다
FAST_ATTEMPTS = 3
# CloseStream 뒤 남은 결과와 Metadata 를 기다리는 상한
DRAIN_TIMEOUT_SEC = 10.0
# Deepgram 이 받았다는 길이와 우리가 보낸 길이의 허용 오차
DURATION_TOLERANCE_MS = 50


def _backoff(attempt: int) -> float:
    return BACKOFF_SEC[min(attempt, len(BACKOFF_SEC)) - 1]


class TakeStream:
    def __init__(
        self,
        take_id: uuid.UUID,
        *,
        stt_adapter: SttAdapter,
        config: SttConfig,
        grace_sec: float | None = None,
    ) -> None:
        self.take_id = take_id
        self._adapter = stt_adapter
        self._config = config
        self._grace_sec = GRACE_SEC if grace_sec is None else grace_sec

        # ── 연결이 바뀌어도 이어지는 상태 ──
        self.sequencer = FrameSequencer()
        self.stt_session_no = 0
        self.segment_no = 1
        self.state: SttState = "connecting"
        # 큐가 넘치거나 Deepgram 이 죽어 있어 STT 에 닿지 못한 오디오
        self.dropped_audio_ms = 0

        # ── Deepgram 세션마다 리셋 ──
        self._base_offset_ms: int | None = None
        self._bytes_sent = 0
        # 드롭된 오디오만큼 다음 프레임 앞에 채울 무음. 타임라인을 밀리지 않게 한다
        self._pending_silence_ms = 0

        self._queue: asyncio.Queue[Accepted | None] = asyncio.Queue(maxsize=QUEUE_MAX_FRAMES)
        self._client: WebSocket | None = None
        self._grace_task: asyncio.Task[None] | None = None
        self._task: asyncio.Task[None] | None = None
        self._ever_connected = False
        self._stopping = False
        self._stop_event = asyncio.Event()
        self._closed = asyncio.Event()

    # ── 수명 ──────────────────────────────────────────────────────────

    def start(self) -> None:
        """백그라운드 태스크로 띄운다. WebSocket 요청 스코프에 묶지 않는다."""
        self._task = asyncio.create_task(self._run(), name=f"take-stream:{self.take_id}")

    async def attach(self, ws: WebSocket) -> None:
        """새 연결을 붙인다. 이미 붙어 있으면 **기존 것을 쫓아낸다.**

        반대(새 연결을 거절) 로 하면 재연결 레이스에서 사용자가 영영 못 붙는다 —
        서버는 아직 옛 연결이 살아 있다고 믿는데 브라우저는 이미 그걸 버린 상태가 된다.
        """
        if self._grace_task is not None:
            self._grace_task.cancel()
            self._grace_task = None

        previous = self._client
        self._client = ws
        if previous is not None and previous is not ws:
            await _quiet(
                previous.send_text(
                    ErrorMessage(
                        code="TAKE_TAKEN_OVER", message="다른 탭에서 같은 연습을 이어받았어요."
                    ).model_dump_json()
                )
            )
            await _quiet(previous.close(code=1008, reason="TAKE_TAKEN_OVER"))

    def detach(self, ws: WebSocket) -> None:
        """연결이 끊겼다. 이미 다른 연결이 붙었으면 아무것도 하지 않는다."""
        if self._client is not ws:
            return
        self._client = None
        if self._stopping:
            return
        self._grace_task = asyncio.create_task(self._expire_grace())

    async def _expire_grace(self) -> None:
        try:
            await asyncio.sleep(self._grace_sec)
        except asyncio.CancelledError:
            return
        logger.info("재연결 없이 grace 만료 take=%s", self.take_id)
        self.request_stop()

    def request_stop(self) -> None:
        """큐에 남은 오디오까지 보내고 CloseStream 으로 정리한다."""
        if self._stopping:
            return
        self._stopping = True
        self._stop_event.set()
        self._put(None)

    async def wait_closed(self, timeout: float = DRAIN_TIMEOUT_SEC) -> bool:
        try:
            await asyncio.wait_for(self._closed.wait(), timeout)
        except TimeoutError:
            logger.warning("Deepgram drain 타임아웃 take=%s", self.take_id)
            return False
        return True

    def cancel(self) -> None:
        """태스크를 즉시 끊는다. 정상 종료(`request_stop`) 와 달리 남은 전사를 버린다."""
        self._stopping = True
        self._stop_event.set()
        if self._grace_task is not None:
            self._grace_task.cancel()
            self._grace_task = None
        if self._task is not None:
            self._task.cancel()
        self.state = "closed"
        self._closed.set()
        forget(self)

    # ── 오디오 투입 ───────────────────────────────────────────────────

    def push(self, data: bytes) -> None:
        """FE 프레임 하나. InvalidAudioFrame 은 호출자가 처리한다."""
        frame = parse_audio_frame(data)
        accepted = self.sequencer.accept(frame)
        if accepted is None:
            return
        self._put(accepted)

    def _put(self, item: Accepted | None) -> None:
        """큐가 차면 **오래된 것부터** 버린다. 최신 오디오가 실시간 코칭에 쓸모 있다."""
        while True:
            try:
                self._queue.put_nowait(item)
                return
            except asyncio.QueueFull:
                dropped = self._queue.get_nowait()
                if dropped is None:
                    # stop 신호를 버리면 영영 안 끝난다. 되돌리고 새 항목을 버린다
                    self._queue.put_nowait(None)
                    if item is not None:
                        self._drop(item)
                    return
                self._drop(dropped)

    def _drop(self, item: Accepted) -> None:
        lost = item.silence_ms + item.frame.duration_ms
        self.dropped_audio_ms += lost
        self._pending_silence_ms += lost

    # ── Deepgram 세션 루프 ────────────────────────────────────────────

    async def _run(self) -> None:
        attempt = 0
        while not self._stopping:
            session = await self._connect()
            if session is None:
                attempt += 1
                if attempt >= FAST_ATTEMPTS:
                    # 재시도는 계속한다. 여기서 연결을 끊으면 FE 1단 코치까지 같이 죽는다
                    await self._set_state("degraded")
                if await self._sleep_or_stop(_backoff(attempt)):
                    break
                continue

            attempt = 0
            self._ever_connected = True
            self.stt_session_no += 1
            self._base_offset_ms = None
            self._bytes_sent = 0
            self._pending_silence_ms = 0
            await self._set_state("ok")

            await self._run_session(session)
            await _quiet(session.abort())
            if self._stopping:
                break
            # 세션만 죽었다. Take 는 계속 간다
            await self._set_state("reconnecting")
            attempt = 1
            if await self._sleep_or_stop(_backoff(attempt)):
                break

        self.state = "closed"
        self._closed.set()
        forget(self)

    async def _connect(self) -> SttSession | None:
        try:
            return await self._adapter.connect(self._config)
        except SttConnectError as e:
            logger.warning("Deepgram 연결 실패 take=%s %s", self.take_id, e)
            return None

    async def _sleep_or_stop(self, delay: float) -> bool:
        """delay 만큼 쉰다. 그 사이 stop 이 오면 True 를 돌려 즉시 끝낸다."""
        try:
            await asyncio.wait_for(self._stop_event.wait(), delay)
        except TimeoutError:
            return False
        return True

    async def _run_session(self, session: SttSession) -> None:
        async with anyio.create_task_group() as tg:

            async def audio() -> None:
                try:
                    await self._pump_audio(session)
                except ConnectionClosed:
                    tg.cancel_scope.cancel()
                    return
                # CloseStream 을 보냈다. 이벤트 펌프가 Metadata 까지 받을 시간을 준다
                await anyio.sleep(DRAIN_TIMEOUT_SEC)
                tg.cancel_scope.cancel()

            async def events() -> None:
                await self._pump_events(session)
                tg.cancel_scope.cancel()

            tg.start_soon(audio)
            tg.start_soon(events)
            tg.start_soon(session.keepalive_loop)

    async def _pump_audio(self, session: SttSession) -> None:
        while True:
            item = await self._queue.get()
            if item is None:
                await session.close_stream()
                return
            await self._send_frame(session, item)

    async def _send_frame(self, session: SttSession, item: Accepted) -> None:
        if self._base_offset_ms is None:
            # 세션의 첫 프레임. 앞의 갭·드롭은 base 가 흡수하므로 무음을 보내지 않는다
            self._base_offset_ms = item.frame.offset_ms
            self._pending_silence_ms = 0
        else:
            gap_ms = item.silence_ms + self._pending_silence_ms
            self._pending_silence_ms = 0
            if gap_ms:
                await self._send_audio(session, silence(gap_ms))
        await self._send_audio(session, item.frame.pcm)

    async def _send_audio(self, session: SttSession, pcm: bytes) -> None:
        await session.send_audio(pcm)
        self._bytes_sent += len(pcm)

    async def _pump_events(self, session: SttSession) -> None:
        async for event in session:
            match event:
                case Transcript():
                    await self._on_transcript(event)
                case Metadata():
                    self._check_duration(event)
                case SttError():
                    logger.warning(
                        "Deepgram 오류 take=%s %s %s", self.take_id, event.code, event.message
                    )
                case _:
                    # UtteranceEnd / SpeechStarted 는 아직 쓰지 않는다
                    pass

    async def _on_transcript(self, event: Transcript) -> None:
        base = self._base_offset_ms or 0
        if event.transcript or event.words:
            # 다음 PR: is_final 이면 여기서 take_transcript_segments 에 저장한다
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

    def _check_duration(self, metadata: Metadata) -> None:
        """Deepgram 이 받았다는 길이와 우리가 보낸 길이를 대조한다.

        어긋나면 그 세션의 타임스탬프가 그만큼 밀려 있다는 뜻이라 리포트에서 영상과 안 맞는다.
        """
        sent_ms = self._bytes_sent // BYTES_PER_MS
        drift_ms = metadata.duration_ms - sent_ms
        level = logger.warning if abs(drift_ms) > DURATION_TOLERANCE_MS else logger.info
        level(
            "Deepgram 세션 종료 take=%s request_id=%s sent_ms=%d duration_ms=%d drift_ms=%+d",
            self.take_id,
            metadata.request_id,
            sent_ms,
            metadata.duration_ms,
            drift_ms,
        )

    # ── FE 로 ─────────────────────────────────────────────────────────

    async def _set_state(self, state: SttState) -> None:
        if self.state == state:
            return
        self.state = state
        await self.send_status()

    async def send_status(self) -> None:
        await self._send(
            SttStatusMessage(
                state=self.state,
                stt_session_no=self.stt_session_no,
                frames=self.sequencer.frames,
                dropped_frames=self.sequencer.dropped,
                silence_ms=self.sequencer.silence_ms,
                lost_ms=self.sequencer.lost_ms + self.dropped_audio_ms,
            )
        )

    async def _send(self, message: BaseModel) -> None:
        """붙어 있는 클라이언트에게만. grace 중이면 조용히 버린다."""
        client = self._client
        if client is None:
            return
        await _quiet(client.send_text(message.model_dump_json()))


async def _quiet(awaitable: Awaitable[object]) -> None:
    """이미 끊긴 상대에게 보내거나 닫을 때 나는 예외는 무시한다.

    정리 경로라서 넓게 잡는다 — 여기서 예외가 새면 그다음 정리가 통째로 멈춘다.
    """
    with contextlib.suppress(Exception):
        await awaitable


# ── 레지스트리 ────────────────────────────────────────────────────────

_streams: dict[uuid.UUID, TakeStream] = {}


async def attach(
    take_id: uuid.UUID,
    ws: WebSocket,
    *,
    stt_adapter: SttAdapter,
    config: SttConfig,
) -> TakeStream:
    """Take 의 스트림을 찾거나 만들고 이 연결을 붙인다."""
    stream = _streams.get(take_id)
    if stream is None or stream.state == "closed":
        stream = TakeStream(take_id, stt_adapter=stt_adapter, config=config)
        _streams[take_id] = stream
        stream.start()
    await stream.attach(ws)
    return stream


def forget(stream: TakeStream) -> None:
    if _streams.get(stream.take_id) is stream:
        del _streams[stream.take_id]


def active_count() -> int:
    return len(_streams)


async def stop_all() -> None:
    """앱 종료. 남은 스트림을 정리한다."""
    streams = list(_streams.values())
    _streams.clear()
    for stream in streams:
        stream.request_stop()
    for stream in streams:
        if not await stream.wait_closed(timeout=2.0):
            stream.cancel()
