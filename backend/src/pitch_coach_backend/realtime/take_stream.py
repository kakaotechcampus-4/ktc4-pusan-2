"""한 Take 의 STT 파이프라인. WebSocket 연결보다 오래 산다.

    FE ──(WS-1)── RealtimeSession ──push──▶ TakeStream ──(WS-2)── Deepgram

연결(`RealtimeSession`)은 탭을 새로 고치면 바뀌지만 `TakeStream` 은 남는다. 그래서
세그먼트 번호·프레임 순서·Deepgram 세션 번호가 재연결 뒤에도 이어진다. 이 값들이 연결 객체
안에 있으면 재연결마다 1 로 리셋되고, `segment_id` 가 `"1-1"` 부터 다시 나와 FE 가
**발표 앞부분 전사를 뒷부분으로 덮어쓴다.**

두 가지 끊김을 따로 다룬다.

- **WS-1** (FE↔BE): 연결만 떨어진다. Deepgram 세션은 `GRACE_SEC` 동안 살려 두고 그 안에
  다시 붙으면 같은 세션에 이어 붙인다. 떨어져 있는 동안 온 final 은 모아 두었다가 재연결 때
  다시 보낸다. 못 붙으면 `CloseStream` 으로 정리한다.
- **WS-2** (BE↔Deepgram): 큐에 담아 두고 백오프로 재접속한다. 새 세션은 타임스탬프가 다시
  0 부터라 `base_offset_ms` 를 새로 잡고 `stt_session_no` 를 올린다. 재접속이 이어서 실패해도
  **연결을 끊지 않는다** — FE 1단 코치는 계속 돌아야 하므로 `degraded` 만 알리고 재시도한다.

타임라인 규칙: 한 Deepgram 세션 안에서는 `take_ms = base_offset_ms + deepgram_ms` 가 성립해야
한다. 그래서 프레임 사이 갭은 무음으로 메운다. 메우기에 너무 긴 갭(`MAX_SILENCE_FILL_MS` 초과)
은 **세션을 갈아** base 를 다시 잡는다 — 일부만 메우면 그 뒤 전사 시각이 통째로 앞당겨진다.

final 은 `TranscriptStore` 로 저장한다 (take 모듈 service 에 위임). 스트림이 새로 만들어질 때는
저장된 마지막 번호를 이어 받는다 — grace 가 만료돼 스트림이 사라진 뒤 다시 붙어도 `segment_id`
가 `"1-1"` 부터 다시 나오지 않아야 FE 가 앞부분을 덮어쓰지 않고, `UNIQUE(take_id, seq)` 도 지켜진다.

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

from pitch_coach_backend.module.take.dto import TranscriptSegmentCreateDTO, TranscriptWordDTO
from pitch_coach_backend.realtime.audio import BYTES_PER_MS, silence
from pitch_coach_backend.realtime.dto import (
    ErrorMessage,
    SttState,
    SttStatusMessage,
    TranscriptMessage,
    WordOut,
    WsErrorCode,
)
from pitch_coach_backend.realtime.event_ingestion import (
    MAX_SILENCE_FILL_MS,
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
from pitch_coach_backend.realtime.transcript_store import TranscriptStore

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
# 정리 중인 스트림에 재연결이 왔을 때 그 정리를 기다리는 상한
HANDOVER_TIMEOUT_SEC = 3.0
# Deepgram 이 받았다는 길이와 우리가 보낸 길이의 허용 오차
DURATION_TOLERANCE_MS = 50
# FE 가 떨어져 있는 동안 모아 둘 final 개수. 10분 발표가 200개 안팎이다
MAX_MISSED_FINALS = 200

# _run_session 이 끝난 이유
_STOPPED = "stopped"  # CloseStream 을 보냈다. Take 가 끝났다
_ROTATE = "rotate"  # 타임라인이 끊겨 새 세션이 필요하다
_LOST = "lost"  # Deepgram 이 끊었다


class StreamOwnerMismatch(Exception):
    """다른 사용자가 이미 쓰고 있는 Take 다."""


def _backoff(attempt: int) -> float:
    return BACKOFF_SEC[min(attempt, len(BACKOFF_SEC)) - 1]


class TakeStream:
    def __init__(
        self,
        take_id: uuid.UUID,
        *,
        owner_id: uuid.UUID,
        stt_adapter: SttAdapter,
        config: SttConfig,
        store: TranscriptStore,
        segment_no: int = 1,
        stt_session_no: int = 0,
        grace_sec: float | None = None,
    ) -> None:
        self.take_id = take_id
        # 이 스트림을 만든 사용자. Take 소유권은 연결마다 service 가 DB 로 검사하므로
        # 정상 흐름에서는 다른 사용자가 여기까지 오지 못한다 — 검사와 레지스트리가
        # 어긋나는 일이 생겨도 남의 스트림에 붙지 못하게 하는 이중 안전장치다
        self.owner_id = owner_id
        self._adapter = stt_adapter
        self._config = config
        self._store = store
        self._grace_sec = GRACE_SEC if grace_sec is None else grace_sec

        # ── 연결이 바뀌어도 이어지는 상태 ──
        self.sequencer = FrameSequencer()
        # 둘 다 Take 기준이다. 스트림이 새로 만들어질 때 저장된 마지막 값에서 이어 받는다
        self.stt_session_no = stt_session_no
        self.segment_no = segment_no
        self.state: SttState = "connecting"
        # 큐가 넘치거나 Deepgram 이 죽어 있어 STT 에 닿지 못한 오디오
        self.dropped_audio_ms = 0

        # ── Deepgram 세션마다 리셋 ──
        self._base_offset_ms: int | None = None
        self._bytes_sent = 0
        # 드롭된 오디오만큼 다음 프레임 앞에 채울 무음. 타임라인을 밀리지 않게 한다
        self._pending_silence_ms = 0
        # 세션을 갈면서 넘긴 프레임. 새 세션의 첫 프레임이 된다
        self._carry: Accepted | None = None

        self._queue: asyncio.Queue[Accepted | None] = asyncio.Queue(maxsize=QUEUE_MAX_FRAMES)
        self._client: WebSocket | None = None
        # FE 가 떨어져 있는 동안 온 final. 다시 붙으면 순서대로 보낸다
        self._missed_finals: list[TranscriptMessage] = []
        # resend_missed() 를 감싼다. 탭 두 개가 거의 동시에 붙으면(예: 재연결이 겹칠 때)
        # 서로 다른 RealtimeSession 이 이 메서드를 동시에 부를 수 있다. 락 없이 같은
        # 리스트를 각자 확인(while)->접근([0])->제거(pop) 하면, await 로 갈라진 그 틈에
        # 한쪽이 이미 비운 자리를 다른 쪽이 또 pop(0) 하다가 IndexError 가 난다
        self._resend_lock = asyncio.Lock()
        self._grace_task: asyncio.Task[None] | None = None
        self._task: asyncio.Task[None] | None = None
        self._ever_connected = False
        self._stopping = False
        self._stop_event = asyncio.Event()
        self._closed = asyncio.Event()

    @property
    def is_stopping(self) -> bool:
        """정리에 들어갔다. 새 연결을 붙이면 안 된다 — 곧 사라질 스트림이다."""
        return self._stopping

    @property
    def is_finished(self) -> bool:
        return self._closed.is_set()

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
                        code=WsErrorCode.TAKE_TAKEN_OVER,
                        message="다른 탭에서 같은 연습을 이어받았어요.",
                    ).model_dump_json()
                )
            )
            await _quiet(previous.close(code=1008, reason=WsErrorCode.TAKE_TAKEN_OVER))

    async def resend_missed(self) -> None:
        """떨어져 있는 동안 온 final 을 순서대로 다시 보낸다.

        `ready` 뒤에 부른다. final 은 이미 DB 에 저장돼 있으므로 리포트에는 영향이 없다 —
        이건 실시간 화면(자막·2단 코치)이 끊긴 구간을 건너뛰지 않게 하는 용도다.

        **성공한 것만 지운다.** 한꺼번에 비우고 보내면, 재연결 직후 클라이언트가
        다시 끊기는 것처럼 중간에 전송이 실패했을 때 아직 못 보낸 나머지가 이미
        비워진 리스트와 함께 통째로 사라진다. 하나씩 확인하며 지워서, 실패하면
        그 지점부터는 다음 재연결 때 다시 시도할 수 있게 남겨 둔다.

        **락으로 감싼다.** 이 메서드가 두 번 동시에 돌면 같은 자리를 두 번 pop 하게 된다.
        """
        async with self._resend_lock:
            while self._missed_finals:
                message = self._missed_finals[0]
                if not await self._send_checked(message):
                    logger.warning(
                        "final 재전송이 중간에 끊겼다 take=%s 남은 개수=%d",
                        self.take_id,
                        len(self._missed_finals),
                    )
                    return
                self._missed_finals.pop(0)
            logger.info("재연결 중 놓친 final 을 모두 다시 보냈다 take=%s", self.take_id)

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

    async def wait_closed(self, timeout: float | None = None) -> bool:
        try:
            await asyncio.wait_for(
                self._closed.wait(), DRAIN_TIMEOUT_SEC if timeout is None else timeout
            )
        except TimeoutError:
            return False
        return True

    def cancel(self) -> None:
        """태스크를 즉시 끊는다. 정상 종료(`request_stop`) 와 달리 남은 전사를 버린다.

        **`cancel()` 은 요청일 뿐이다.** `self._task.cancel()` 은 다음 await 지점에서
        `CancelledError` 를 던지도록 예약할 뿐, 그 자리에서 정리가 끝나는 게 아니다.
        실제 정리(Deepgram 소켓을 shield 안에서 닫는 것)는 `_run` 의 finally 가 한다.

        **`_run` 이 한 번도 스텝되지 않은 채로 취소되면 그 finally 조차 안 돈다.**
        `asyncio.create_task()` 로 만든 태스크는 아직 "실행 중" 이 아니라 "예약됨" 상태다.
        이 상태에서 바로 `.cancel()` 하면 코루틴 본문이 단 한 줄도 실행되지 않고 그대로
        취소 처리된다 (직접 확인함). `not self._task.done()` 만으로는 이 경우와
        "정말 돌고 있어서 finally 가 곧 돈다" 를 구분할 수 없으므로, 취소 결과를
        직접 기다렸다가 `_finish()` 가 안 불렸으면 여기서 마무리한다 (idempotent).
        """
        self._stopping = True
        self._stop_event.set()
        if self._grace_task is not None:
            self._grace_task.cancel()
            self._grace_task = None
        if self._task is None:
            self._finish()
            return
        if self._task.done():
            # 이미 끝났다. finally 가 돌았을 테지만 _finish() 는 idempotent 하다
            self._finish()
            return
        self._task.cancel()
        asyncio.create_task(self._reap_after_cancel())

    async def _reap_after_cancel(self) -> None:
        """취소가 실제로 끝나는 걸 기다렸다가, `_run` 의 finally 가 못 불렀으면 대신 부른다."""
        if self._task is not None:
            with contextlib.suppress(BaseException):
                await self._task
        self._finish()

    def _finish(self) -> None:
        if self._closed.is_set():
            return
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
        """Deepgram 세션을 필요한 만큼 이어서 연다.

        **루프 조건에 `not self._stopping` 을 두지 않는다.** 회전(`_ROTATE`)과 stop 이
        겹치면(회전 직전에 넘겨받은 `self._carry` 오디오 한 프레임이 아직 안 나갔는데
        `request_stop()` 이 먼저 온 경우) `_stopping` 은 이미 True 다. 거기서 루프
        조건만으로 재진입을 막으면 `continue` 를 써도 다음 세션이 절대 안 열려서
        캐리 프레임과 그 뒤 큐에 쌓인 stop 신호(`None`) 를 통째로 못 보내고 끝난다.
        그래서 재진입 여부는 오직 `_run_session` 의 반환값(`reason`) 으로만 정한다.
        """
        attempt = 0
        try:
            while True:
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

                reason = await self._run_session(session)
                if reason == _ROTATE:
                    # stop 이 회전과 동시에 왔어도 새 세션을 반드시 한 번 더 연다 — 그래야
                    # self._carry 와 큐에 남은 나머지(stop 신호 포함) 가 새 세션에서
                    # 정상적으로 드레인되고, 그 세션이 스스로 _STOPPED 로 끝난다
                    continue
                if reason == _STOPPED:
                    # CloseStream 을 이미 보냈다. 더 열 세션이 없다
                    break
                # _LOST — Deepgram 이 끊었다. stop 까지 요청된 상태라면 재시도하지 않는다
                if self._stopping:
                    break
                await self._set_state("reconnecting")
                attempt = 1
                if await self._sleep_or_stop(_backoff(attempt)):
                    break
        finally:
            self._finish()

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

    async def _run_session(self, session: SttSession) -> str:
        reason = _LOST
        try:
            async with anyio.create_task_group() as tg:

                async def audio() -> None:
                    nonlocal reason
                    try:
                        reason = await self._pump_audio(session)
                    except ConnectionClosed:
                        reason = _LOST
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
        finally:
            # 취소로 빠져나가도 Deepgram 소켓은 닫는다. shield 가 없으면 이 await 가
            # 다시 취소되어 연결이 그대로 남는다
            with anyio.CancelScope(shield=True):
                await _quiet(session.abort())
        return reason

    async def _pump_audio(self, session: SttSession) -> str:
        while True:
            item = self._carry or await self._queue.get()
            self._carry = None
            if item is None:
                await session.close_stream()
                return _STOPPED

            gap_ms = item.silence_ms + self._pending_silence_ms
            if self._base_offset_ms is not None and (
                item.timeline_break or gap_ms > MAX_SILENCE_FILL_MS
            ):
                # 무음으로 메우기엔 너무 긴 갭이다. 이대로 보내면 이후 전사 시각이
                # 갭만큼 앞당겨진다. 세션을 갈아 base 를 다시 잡는다
                logger.info(
                    "타임라인 단절로 Deepgram 세션을 교체한다 take=%s gap_ms=%d",
                    self.take_id,
                    gap_ms + item.lost_ms,
                )
                self._carry = item
                await session.close_stream()
                return _ROTATE

            await self._send_frame(session, item, gap_ms)

    async def _send_frame(self, session: SttSession, item: Accepted, gap_ms: int) -> None:
        if self._base_offset_ms is None:
            # 세션의 첫 프레임. 앞의 갭·드롭은 base 가 흡수하므로 무음을 보내지 않는다
            self._base_offset_ms = item.frame.offset_ms
            self._pending_silence_ms = 0
        else:
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
            message = TranscriptMessage(
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
            if event.is_final and self._client is None:
                # FE 가 떨어져 있다. interim 은 어차피 다음 것이 덮으니 버리고 final 만 모은다
                self._missed_finals.append(message)
                del self._missed_finals[:-MAX_MISSED_FINALS]
            else:
                await self._send(message)
            if event.is_final:
                # 화면에 먼저 보내고 저장한다. 저장은 클라이언트가 붙어 있든 없든 한다 —
                # 리포트의 원천은 이 행이지 화면이 아니다
                await self._persist(message)
        if event.is_final:
            # 빈 final 도 구간을 닫는다. 다음 interim 은 새 번호로
            self.segment_no += 1

    async def _persist(self, message: TranscriptMessage) -> None:
        segment = TranscriptSegmentCreateDTO(
            seq=self.segment_no,
            stt_session_no=self.stt_session_no,
            start_ms=message.start_ms,
            end_ms=message.end_ms,
            transcript=message.text,
            words=[
                TranscriptWordDTO(
                    word=w.word,
                    punctuated_word=w.punctuated_word,
                    start_ms=w.start_ms,
                    end_ms=w.end_ms,
                    confidence=w.confidence,
                )
                for w in message.words
            ],
            confidence=message.confidence,
            speech_final=message.speech_final,
        )
        try:
            await self._store.append(self.take_id, segment)
        except Exception:
            # DB 장애로 스트림까지 멈추면 안 된다 — 화면 전사는 이미 나갔고 발표는 계속된다.
            # 전사 원문은 로그에 남기지 않는다 (seq 와 길이만)
            logger.exception(
                "final 저장 실패 take=%s seq=%d len=%d",
                self.take_id,
                segment.seq,
                len(segment.transcript),
            )

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

    async def _send_checked(self, message: BaseModel) -> bool:
        """`_send` 와 같지만 성공 여부를 알려준다. `resend_missed` 가 재시도를 판단하는 데 쓴다.

        일반 전사·상태 메시지는 실패해도 다음 메시지가 금방 또 오니 무시해도 되지만
        (`_send`), 놓친 final 은 화면에 **다시 안 오면 그 구간이 비는 데이터**라 구분한다.
        """
        client = self._client
        if client is None:
            return False
        try:
            await client.send_text(message.model_dump_json())
        except Exception:  # noqa: BLE001 - 어떤 전송 실패든 "재시도 대상" 으로 취급한다
            return False
        return True


async def _quiet(awaitable: Awaitable[object]) -> None:
    """이미 끊긴 상대에게 보내거나 닫을 때 나는 예외는 무시한다.

    정리 경로라서 넓게 잡는다 — 여기서 예외가 새면 그다음 정리가 통째로 멈춘다.
    """
    with contextlib.suppress(Exception):
        await awaitable


# ── 레지스트리 ────────────────────────────────────────────────────────

_streams: dict[uuid.UUID, TakeStream] = {}

# attach() 의 "확인 -> 생성" 이 원자적이어야 한다. 아니면 grace 만료 직후처럼 같은
# take_id 로 재연결이 동시에 두 개 오는 순간 **둘 다** `_streams.get()` 에서 "없다" 를
# 보고 각자 TakeStream·Deepgram 세션을 만든다. 그러면 하나는 `_streams` 덮어쓰기로
# 레지스트리에서 밀려나 고아가 되고, Deepgram 연결은 둘 생긴다.
#
# 프로세스 전체에 락 하나로 충분하다 — attach() 는 연결이 열릴 때 한 번만 타는 경로라
# 자주 불리지 않고(오디오 프레임이 오가는 push() 는 이 락을 안 탄다), take_id 별로
# 나누는 복잡성을 들일 이유가 없다. is_stopping 핸드오버(최대 HANDOVER_TIMEOUT_SEC) 동안
# 다른 take_id 의 attach() 도 같이 멈추지만, 그 경로 자체가 "재연결이 정리 타이밍과
# 정확히 겹칠 때" 만 타는 드문 경로라 MVP 규모에서는 감수한다.
_attach_lock = asyncio.Lock()


async def attach(
    take_id: uuid.UUID,
    ws: WebSocket,
    *,
    owner_id: uuid.UUID,
    stt_adapter: SttAdapter,
    config: SttConfig,
    store: TranscriptStore,
) -> TakeStream:
    """Take 의 스트림을 찾거나 만들고 이 연결을 붙인다.

    Take 존재·소유·상태는 호출자(service)가 DB 로 먼저 검사한다. 여기서는 스트림을
    만든 사용자와 다르면 `StreamOwnerMismatch` — 그 검사의 이중 안전장치다.
    """
    async with _attach_lock:
        stream = _streams.get(take_id)

        if stream is not None and stream.is_stopping:
            # 정리 중인 스트림에 붙이면 곧 사라질 객체에 오디오를 밀어 넣게 된다.
            # 끝나기를 기다렸다가 새로 만든다
            if not await stream.wait_closed(timeout=HANDOVER_TIMEOUT_SEC):
                # 정상 drain 은 최대 DRAIN_TIMEOUT_SEC(10s) 까지 걸릴 수 있는데
                # 핸드오버는 HANDOVER_TIMEOUT_SEC(3s) 만 기다린다. 여기서 그냥
                # forget() 만 하면 옛 스트림은 레지스트리에서만 사라질 뿐 Deepgram
                # 연결을 붙든 채 자기 페이스대로 계속 돈다 — "정리" 가 아니라 "방치" 다.
                # 강제로 끊는다
                stream.cancel()
            forget(stream)
            stream = None

        if stream is not None and stream.is_finished:
            forget(stream)
            stream = None

        if stream is None:
            # 저장된 마지막 번호에서 이어 받는다. 이 스트림이 처음이면 (0, 0) 이라 1·0 부터
            last_seq, last_session_no = await store.cursor(take_id)
            stream = TakeStream(
                take_id,
                owner_id=owner_id,
                stt_adapter=stt_adapter,
                config=config,
                store=store,
                segment_no=last_seq + 1,
                stt_session_no=last_session_no,
            )
            _streams[take_id] = stream
            stream.start()
        elif stream.owner_id != owner_id:
            raise StreamOwnerMismatch

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
