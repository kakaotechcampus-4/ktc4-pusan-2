"""한 Take 의 STT 파이프라인. WebSocket 연결보다 오래 산다.

    FE ──(WS-1)── RealtimeSession ──push──▶ TakeStream ──(WS-2)── Deepgram

연결(`RealtimeSession`)은 탭을 새로고침하면 바뀌지만 `TakeStream` 은 남는다. 그래서
세그먼트 번호·프레임 순서·Deepgram 세션 번호가 재연결 뒤에도 이어진다. 이 값들이 연결 객체
안에 있으면 재연결마다 1 로 리셋되고, `segment_id` 가 `"1-1"` 부터 다시 나와 FE 가
**발표 앞부분 전사를 뒷부분으로 덮어쓴다.**

두 가지 끊김을 따로 다룬다.

- **WS-1** (FE↔BE): 연결만 떨어진다. Deepgram 세션은 `GRACE_SEC` 동안 살려 두고 그 안에
  다시 붙으면 같은 세션에 이어 붙인다. 떨어져 있는 동안 온 final 은 모아 두었다가 재연결 때
  다시 보낸다. 못 붙으면 `CloseStream` 으로 정리한다.
  탭을 새로고침하면 FE 는 seq 를 1 부터 다시 세고 offset 은 이어받은 무대 시계부터 보낸다.
  새 연결의 첫 프레임이 뒤로 가면(seq 또는 offset) 그렇게 보고(`Accepted.restart`) 세션을 갈아
  그 offset 으로 base 를 잡는다. grace 가 지난 뒤 새 스트림이 열리는 경우와 같은 결과다.
- **WS-2** (BE↔Deepgram): 큐에 담아 두고 백오프로 재접속한다. 재접속 · 세션별 시간 기준 · 무음
  채우기 · 세션 갈기는 `stt/loop.py` 가 맡는다. 이 파일은 큐를 채우고 돌려받은 전사를 처리한다.

final 은 `TranscriptStore` 로 저장한다 (take 모듈 service 에 위임). 스트림이 새로 만들어질 때는
저장된 마지막 번호를 이어 받는다 — grace 가 만료돼 스트림이 사라진 뒤 다시 붙어도 `segment_id`
가 `"1-1"` 부터 다시 나오지 않아야 FE 가 앞부분을 덮어쓰지 않고, `UNIQUE(take_id, seq)` 도 지켜진다.
저장에 실패한 final 은 들고 있다가 다음 final 을 저장할 때와 스트림이 정상으로 끝날 때 다시
저장한다.

스트림 정리와 Take 종료는 다르다. 사용자가 stop 을 보내면(`end`) Take 가 끝나 같은 Take 로 다시
붙지 못한다. grace 만료 · 오디오 없는 연결 · 앱 종료는 스트림만 정리한다 — FE 는 끊긴 뒤에도
다시 붙고, 새 스트림이 번호를 이어 받는다. 그렇게 정리된 뒤 `ABANDON_AFTER_SEC` 동안 아무도
다시 붙지 않으면 사용자가 떠난 것으로 보고 Take 를 `FAILED`(`ABANDONED`)로 끝낸다(`abandon.py`).
재연결을 포기한 FE 는 STT 없이 발표를 이어 가는 중일 수 있다 — 그 FE 가 끝내기 PUT 을 보내면
되살린다 (take repository `mark_ended`). 창을 닫아(닫힘 코드 1001) 끊겼으면 `CLOSED_TAB_WAIT_SEC`
만 기다리고 이유를 `CLOSED` 로 적는다 — 창을 닫은 Take 는 이어 가지 않는다. 그 안에 다시 붙는 것은
새로고침이다.
이 타이머는 메모리에만 있어 앱이 다시 뜨면 DB 를 보고 다시 건다(`registry.arm_abandoned_takes`).

자동 종료(`auto_end.py`): 말 없음이나 최대 시간에 걸리면 사용자가 stop 을 보낸 것과 같은 순서로
정리한다 — `auto_end` 를 알리고, 남은 오디오를 흘리고, `stt_status` closed 를 보내고 1000 으로
닫는다. Take 를 `ANALYZING` 으로 바꾸고 이유를 DB 에 남겨 같은 Take 로 다시 붙지 못하게 한다.
말 없음 기록과 서버 시계는 스트림이 아니라 Take 에 붙어 있어(`TakeRecord`) 스트림이 새로 열려도
이어진다.

실시간 코치(`coach/session.py`): Take 에 붙어 있어 스트림이 바뀌어도 이어진다. `metrics` 마다
`/coach/evaluate` 를 부르고, 확정 단어에 확정 시각(`final_at_ms`)을 붙이고, 문장 끝 신호 · 장 변경을
모은다. Take 를 끝내면(`finish.finish_take`) 판단을 멈추고 마지막 창을 만들어, 문장 끝 신호와 함께
Take 를 `ANALYZING` · `FINALIZING` 으로 적은 뒤 Take 결과(`coach/finalize.py`)를 백그라운드로
띄운다.

스트림 맵 · 끝낸 Take · Take 별 기록은 `state.py` 가 들고 있다. 이 파일은 registry 를 import 하지
않는다. 받은 기록(`record.TakeRecord`)을 갱신하고 알릴 일은 `record.StreamHost` 로 넘긴다.
"""

import asyncio
import contextlib
import logging
import time
import uuid

import anyio
from fastapi import WebSocket

from pitch_coach_backend.coach.replies import EvaluateReply
from pitch_coach_backend.coach.requests import SlideEvent
from pitch_coach_backend.coach.session import MAX_SLIDE_CHANGES
from pitch_coach_backend.module.take.dto import (
    MetricSecondCreateDTO,
)
from pitch_coach_backend.realtime.dto import (
    AutoEndMessage,
    AutoEndReason,
    CoachMessage,
    ErrorMessage,
    MetricsMessage,
    SlideMessage,
    SttState,
    SttStatusMessage,
    TranscriptMessage,
    WordOut,
    WsErrorCode,
)
from pitch_coach_backend.realtime.event_ingestion import (
    Accepted,
    FrameSequencer,
    parse_audio_frame,
)
from pitch_coach_backend.realtime.storage.store import TranscriptStore
from pitch_coach_backend.realtime.storage.transcripts import TranscriptBuffer
from pitch_coach_backend.realtime.stream.audio_queue import AudioQueue
from pitch_coach_backend.realtime.stream.auto_end import AutoEndPolicy, TimeLimit
from pitch_coach_backend.realtime.stream.client import ClientSlot, close_with, quiet
from pitch_coach_backend.realtime.stream.heard import HeardRange
from pitch_coach_backend.realtime.stream.record import CoachDeps, StreamHost, TakeRecord
from pitch_coach_backend.realtime.stt import loop as stt_loop
from pitch_coach_backend.realtime.stt.adapter import (
    SttAdapter,
    SttConfig,
    Transcript,
)
from pitch_coach_backend.realtime.stt.loop import SttLoop

logger = logging.getLogger(__name__)

# FE 가 끊긴 뒤 Deepgram 세션을 살려 두는 시간. 탭 새로고침·와이파이 전환을 덮는다
GRACE_SEC = 30.0
# Deepgram 이 죽어 있는 동안 들고 있을 오디오. 100ms 프레임 기준 5초
QUEUE_MAX_FRAMES = 50
# 자동 종료를 알린 뒤 FE 가 마지막 1초 조각과 stop 을 보내기를 기다리는 상한. 그 전에 끝난 것으로
# 적으면 마지막 조각이 빠진다. stop 을 보내지 않는 FE 도 있어 오래 기다리지 않는다
FINAL_METRICS_WAIT_SEC = 2.0
# 상태 · 자동 종료 알림은 이 시간 안에 보내지 못해도 STT 와 종료 정리를 계속한다
CONTROL_SEND_TIMEOUT_SEC = 1.0
# gaze · voice 가 둘 다 없는 metrics 의 1초 길이. FE 는 1초마다 보낸다
METRICS_PERIOD_MS = 1_000
# 연결이 붙어 있고 이 시간 안에 오디오가 왔으면 발표 중으로 본다. 창을 다시 연 FE 가 Take 를
# 실패로 끝내려 해도 거절한다 (`is_live`). FE 가 다른 창이 살아 있다고 보는 하트비트 기준과 같다
LIVE_AUDIO_SEC = 10.0


class TakeStream:
    def __init__(
        self,
        take_id: uuid.UUID,
        *,
        owner_id: uuid.UUID,
        stt_adapter: SttAdapter,
        config: SttConfig,
        store: TranscriptStore,
        record: TakeRecord,
        host: StreamHost,
        segment_no: int = 1,
        stt_session_no: int = 0,
        grace_sec: float | None = None,
        auto_end: AutoEndPolicy | None = None,
        coach: CoachDeps | None = None,
    ) -> None:
        self.take_id = take_id
        # 이 스트림을 만든 사용자. Take 소유권은 연결마다 service 가 DB 로 검사하므로
        # 정상 흐름에서는 다른 사용자가 여기까지 오지 못한다 — 검사와 레지스트리가
        # 어긋나는 일이 생겨도 남의 스트림에 붙지 못하게 하는 이중 안전장치다
        self.owner_id = owner_id
        self._store = store
        self._grace_sec = GRACE_SEC if grace_sec is None else grace_sec
        self.auto_end_policy = AutoEndPolicy.from_settings() if auto_end is None else auto_end
        self._coach = coach
        self._host = host

        # ── 연결이 바뀌어도 이어지는 상태 ──
        self.sequencer = FrameSequencer()
        self.state: SttState = "connecting"
        # 정리(closed)되기 전 마지막 상태. 자동 종료 뒤 FE 가 보내는 마지막 1초 조각의 STT 상태다
        self._open_state: SttState = "connecting"
        # 큐가 넘치거나 Deepgram 이 죽어 있어 STT 에 닿지 못한 오디오
        self.dropped_audio_ms = 0
        # 말 없음 기록 · 서버 시계 · 1초 원자료. Take 에 붙어 있어 이 스트림보다 오래 산다
        self._record = record
        self._slides_capped = False
        # 마지막으로 받은 오디오 프레임 시각(monotonic). 발표 중인지 본다 (`is_live`)
        self._last_audio_at: float | None = None
        # 지금 연결이 붙은 시각(monotonic). 오디오가 멈춘 연결인지 본다 (`is_idle`)
        self._attached_at: float | None = None
        # 스트림이 교체되어도 Take의 오디오 예산은 이어 쓴다.
        self._budget = record.audio_budget
        self._over_budget_logged = False
        # 끊김 없이 받은 오디오의 Take 시각. 1초 원자료의 STT 상태를 이걸로 정한다
        self._heard = HeardRange()
        # 최대 시간. start() 에서 서버 시계를 켠다
        self._time_limit = TimeLimit(
            self.auto_end_policy, lambda: self._auto_end(AutoEndReason.TIME_LIMIT)
        )

        # Deepgram 으로 보낼 오디오. 넘쳐 버린 것은 `_drop` 이 유실로 센다
        self._audio = AudioQueue(QUEUE_MAX_FRAMES, self._drop)
        # 지금 붙어 있는 FE 연결. 끊긴 뒤 다시 붙기를 기다리는 동안은 비어 있다
        self._client = ClientSlot()
        # 확정 전사의 번호 · 저장 재시도 · 화면에 못 보낸 final. 번호는 저장된 마지막 값에서 이어
        # 받는다
        self._transcripts = TranscriptBuffer(take_id, store, segment_no=segment_no)
        # 놓친 final 을 다시 보내기 시작한 연결 (`resend_missed`). ready 를 받은 연결이다
        self._caught_up: WebSocket | None = None
        self._grace_task: asyncio.Task[None] | None = None
        self._task: asyncio.Task[None] | None = None
        # cancel() 뒤 _run 의 정리를 기다리는 태스크. 참조를 들고 있어야 한다 — asyncio 는
        # 태스크를 약하게만 잡고 있어서, 아무도 참조하지 않는 태스크는 GC 에 끝나기 전에
        # 사라질 수 있다 (asyncio.create_task 문서의 경고)
        self._reaper: asyncio.Task[None] | None = None
        # Take 가 지워져 끊었다 (`discard`). 붙어 있던 연결에 알리는 태스크도 위와 같은 이유로 든다
        self._discarded = False
        self._closer: asyncio.Task[None] | None = None
        # 다른 Take 에 자리를 내주며 붙어 있던 연결을 닫는 태스크 (`release_idle`)
        self._releaser: asyncio.Task[None] | None = None
        # 마지막 연결이 창을 닫아(닫힘 코드 1001) 끊겼다. 다시 붙으면 지운다
        self._tab_closed = False
        self._stopping = False
        # BE 가 자동으로 끝냈으면 그 이유. 사용자가 그 뒤에 보낸 stop 도 같은 이유로 적는다
        self.auto_end_reason: AutoEndReason | None = None
        self._stop_event = asyncio.Event()
        self._closed = asyncio.Event()
        # Deepgram 세션 루프. 위 큐를 꺼내 보내고 전사 · 상태를 아래 콜백으로 돌려준다
        self._stt = SttLoop(
            take_id,
            adapter=stt_adapter,
            config=config,
            queue=self._audio.queue,
            stop_event=self._stop_event,
            is_stopping=lambda: self._stopping,
            on_state=lambda state: self._set_state(state),
            on_transcript=lambda event: self._on_transcript(event),
            on_utterance_end=lambda take_ms: self._record.coach.on_utterance_end(take_ms),
            stt_session_no=stt_session_no,
        )

    @property
    def stt_session_no(self) -> int:
        """지금 Deepgram 세션 번호 (Take 기준, `SttLoop`)."""
        return self._stt.stt_session_no

    @property
    def is_stopping(self) -> bool:
        """정리에 들어갔다. 새 연결을 붙이면 안 된다 — 곧 사라질 스트림이다."""
        return self._stopping

    @property
    def is_finished(self) -> bool:
        return self._closed.is_set()

    @property
    def is_receiving_audio(self) -> bool:
        """연결이 붙어 있고 최근 `LIVE_AUDIO_SEC` 안에 오디오가 왔다. 어느 창에서 발표 중이다.

        브라우저가 닫힘 프레임 없이 사라진 연결은 붙어 있어도 오디오가 멈춰 있어 False 다.
        """
        return (
            self._client.connected
            and self._last_audio_at is not None
            and time.monotonic() - self._last_audio_at < LIVE_AUDIO_SEC
        )

    @property
    def is_idle(self) -> bool:
        """연결이 붙어 있지만 붙은 뒤 · 마지막 오디오 뒤 `LIVE_AUDIO_SEC` 가 지났다.

        브라우저가 닫힘 프레임 없이 사라진 연결이다 — 서버는 ping 이 끊을 때까지 붙어 있다고 본다.
        막 붙어 아직 오디오를 보내기 전인 연결은 아니다.
        """
        if not self._client.connected or self._attached_at is None:
            return False
        last = max(self._attached_at, self._last_audio_at or self._attached_at)
        return time.monotonic() - last >= LIVE_AUDIO_SEC

    @property
    def has_client(self) -> bool:
        """연결이 붙어 있다. 끊긴 뒤 다시 붙기를 기다리는 동안은 False."""
        return self._client.connected

    # ── 수명 ──────────────────────────────────────────────────────────

    def start(self) -> None:
        """백그라운드 태스크로 띄운다. WebSocket 요청 스코프에 묶지 않는다."""
        # 다시 붙었다. 떠난 것으로 보고 끝내려던 타이머를 끈다
        self._host.stream_started(self.take_id)
        self._task = asyncio.create_task(self._run(), name=f"take-stream:{self.take_id}")
        self._time_limit.start(self._record.streamed_sec)

    async def attach(self, ws: WebSocket) -> None:
        """새 연결을 붙인다. 이미 붙어 있으면 기존 것을 쫓아낸다 (`ClientSlot.attach`)."""
        if self._grace_task is not None:
            self._grace_task.cancel()
            self._grace_task = None

        self._tab_closed = False
        # 코치의 한마디는 지금 붙어 있는 연결로 보낸다. 스트림이 새로 열려도 세션은 이어진다.
        # 새 연결은 상태 표시를 모르므로 다음 응답의 표시를 바뀐 것으로 보고 보낸다
        self._record.coach.notify = self._notify_coach
        self._record.coach.forget_display()
        self.sequencer.new_connection()
        self._attached_at = time.monotonic()
        await self._client.attach(ws)

    async def resend_missed(self, source: WebSocket | None = None) -> None:
        """떨어져 있는 동안 온 final 을 순서대로 다시 보낸다 (`TranscriptBuffer.resend`).

        `ready` 뒤에 부른다. 실시간 화면(자막·2단 코치)이 끊긴 구간을 건너뛰지 않게 하는 용도다.
        """
        target = self._client.ws if source is None else source
        if target is None or not self._client.holds(target):
            return
        self._caught_up = target

        async def send(message: TranscriptMessage) -> bool:
            return await self._send_final_to(message, target)

        await self._transcripts.resend(send)

    def is_client(self, ws: WebSocket) -> bool:
        """지금 붙어 있는 연결인가. 다른 탭이 이어받아 쫓겨난 연결이면 False."""
        return self._client.holds(ws)

    def detach(self, ws: WebSocket, *, tab_closed: bool = False) -> None:
        """연결이 끊겼다. 이미 다른 연결이 붙었으면 아무것도 하지 않는다.

        `tab_closed` 는 브라우저가 창을 닫거나 새로고침해 끊은 것이다 (닫힘 코드 1001). 아무도 다시
        붙지 않으면 짧게 기다렸다 끝낸다 (`_finish`).
        """
        if not self._client.holds(ws):
            return
        self._client.detach()
        self._caught_up = None
        self._tab_closed = tab_closed
        # 다시 붙을지 모른다. 모아 둔 1초 원자료를 지금 넣어 둔다
        self._record.metrics.flush_soon()
        if self._stopping:
            return
        if self.sequencer.frames == 0:
            # 오디오가 한 번도 안 왔다. 살려 둘 전사가 없으니 grace 동안 Deepgram 세션만 붙들고
            # 있을 이유가 없다 — 종료 중에 새로고침한 FE 가 stop 만 보내러 붙었다가 기다리지
            # 못하고 끊은 경우가 이렇다. 다시 붙으면 저장된 마지막 번호에서 새 스트림을 연다
            self.request_stop()
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
        self._audio.put(None)

    def end(self) -> None:
        """사용자가 Take 를 끝냈다. 스트림을 정리하고, 같은 Take 로 다시 붙지 못하게 한다.

        `request_stop` 만으로는 Take 가 끝나지 않는다 — grace 만료나 오디오 없는 연결도 스트림을
        정리하지만, 그 뒤 FE 가 다시 붙으면 새 스트림으로 이어 가야 한다.
        이미 자동으로 끝낸 뒤 온 stop(FE 가 `auto_end` 를 받고 보낸 것)이면 그 이유를 지우지 않는다.
        """
        self._host.take_ended(self.take_id, None)
        # 새 판단은 보내지 않는다 (1-3 3번 ①). 진행 중인 것은 Take 를 끝낼 때 기다린다
        self._record.coach.stop()
        # stop 앞에 온 마지막 1초 조각은 이미 받았다
        self._client.done.set()
        self.request_stop()

    async def wait_closed(self, timeout: float | None = None) -> bool:
        try:
            await asyncio.wait_for(
                self._closed.wait(), stt_loop.DRAIN_TIMEOUT_SEC if timeout is None else timeout
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
        self._reaper = asyncio.create_task(
            self._reap_after_cancel(), name=f"take-stream-reap:{self.take_id}"
        )

    async def _reap_after_cancel(self) -> None:
        """취소가 실제로 끝나는 걸 기다렸다가, `_run` 의 finally 가 못 불렀으면 대신 부른다."""
        if self._task is not None:
            with contextlib.suppress(BaseException):
                await self._task
        self._finish()

    async def wait_cancelled(self, timeout: float) -> bool:
        """`cancel()` 뒤의 정리를 기다린다. 제때 안 끝나면 **닫힌 것으로 확정**하고 False.

        정리가 늦는 경우는 사실상 하나다 — Deepgram 소켓 닫기(abort) 가 상대 응답을
        기다리는 것. 그건 `ABORT_TIMEOUT_SEC` 안에 어차피 끝나고, 그 뒤엔 reaper 가
        `_finish()` 를 다시 불러도 idempotent 라 문제없다. 기다리는 쪽(FE 의 stop 응답,
        핸드오버) 이 그 소켓 하나 때문에 더 붙잡혀 있을 이유는 없다.
        """
        if await self.wait_closed(timeout):
            return True
        logger.error(
            "취소 뒤 %.1fs 안에 정리가 안 끝났다 take=%s. 닫힌 것으로 확정한다",
            timeout,
            self.take_id,
        )
        self._finish()
        return False

    def discard(self) -> None:
        """Take 가 지워졌다. 남은 전사를 기다리지 않고 끊고, 붙어 있는 연결에 알린 뒤 닫는다.

        저장할 행이 없으니 정상 종료(`request_stop`)처럼 남은 오디오를 흘려 전사를 받을 이유가
        없다. FE 에는 다시 붙을 때 받을 것과 같은 `TAKE_NOT_FOUND` 를 보낸다 — FE 는 이걸 받으면
        다시 붙지 않는다.

        스트림 태스크 안(`_on_transcript`)에서도 부른다. 연결을 닫는 일을 따로 태스크로 돌리는
        이유다 — 여기서 기다리면 `cancel()` 이 그 자리를 끊어 알림이 안 나간다.
        """
        self._cut(ErrorMessage(code=WsErrorCode.TAKE_NOT_FOUND, message="연습을 찾을 수 없습니다."))

    def release_idle(self) -> None:
        """다른 Take 에 자리를 내준다 (`registry._make_room`). 오디오가 멈춘 연결(`is_idle`)을
        `TOO_MANY_STREAMS` 로 닫고 남은 오디오까지 보내 정리한다.

        Take 는 끝내지 않는다 — 연결이 끊긴 채 정리된 스트림과 같다. 닫힌 연결이 뒤늦게 `detach`
        해도 이미 떼었으므로 아무것도 하지 않는다.
        """
        client = self._client.detach()
        self._caught_up = None
        if client is not None:
            self._releaser = asyncio.create_task(
                close_with(
                    client,
                    ErrorMessage(
                        code=WsErrorCode.TOO_MANY_STREAMS,
                        message="다른 곳에서 새 연습을 시작해 이 연결을 닫았어요.",
                    ),
                ),
                name=f"take-stream-release:{self.take_id}",
            )
        self.request_stop()

    def drop_closed_tab(self, *, replaced: bool = False) -> None:
        """창을 다시 연 FE 가 이 Take 를 실패로 끝냈다. 남은 전사를 기다리지 않고 끊는다.

        분석하지 않는 Take 라 전사를 받을 이유가 없다. 붙어 있는 연결(브라우저는 이미 닫았는데
        서버가 아직 모르는 연결)에는 다시 붙을 때 받을 것과 같은 `TAKE_ENDED` 를 보낸다.
        `replaced` 면 다른 곳에서 새 연습을 시작해 끝낸 것이다. 그 창은 아직 발표 중이었을 수 있어
        문구로 알린다 — 이유는 같은 `CLOSED` 라 FE 는 다시 붙지 않고 이어 가지도 않는다.
        """
        message = (
            "다른 곳에서 새 연습을 시작해 이 연습을 끝냈어요."
            if replaced
            else "이미 끝난 연습입니다."
        )
        self._cut(ErrorMessage(code=WsErrorCode.TAKE_ENDED, message=message, end_reason="CLOSED"))

    async def sign_out(self) -> None:
        """사용자가 로그아웃했다. 붙어 있는 연결에 `UNAUTHORIZED` 를 알리고 닫는다.

        연결을 떼지 않는다. 닫힌 연결이 평소처럼 `detach` 해 grace 를 건다 — 여기서 떼면 그 `detach`
        가 아무것도 하지 않아 grace 가 걸리지 않는다.
        """
        client = self._client.ws
        if client is not None:
            await close_with(
                client,
                ErrorMessage(
                    code=WsErrorCode.UNAUTHORIZED, message="로그아웃했습니다. 다시 로그인해 주세요."
                ),
            )

    def _cut(self, error: ErrorMessage) -> None:
        if self._discarded:
            return
        self._discarded = True
        # 분석하지 않는 Take 다 (지웠거나 창을 닫아 실패로 끝냄)
        self._record.coach.release()
        # 끊은 스트림의 전사·상태가 이 연결로 더 나가지 않게 먼저 뗀다
        client = self._client.detach()
        self.cancel()
        if client is not None:
            self._closer = asyncio.create_task(
                close_with(client, error), name=f"take-stream-cut:{self.take_id}"
            )

    def _finish(self) -> None:
        if self._closed.is_set():
            return
        self._record.streamed_sec += self._time_limit.stop()
        # Deepgram 에 닿지 못한 채 큐에 남은 오디오는 유실이다. 마지막 stt_status 의
        # lost_ms 가 이걸 빼먹으면 리포트가 "STT 다 받았다" 고 잘못 말한다
        self._audio.drain()
        self._transcripts.report_leftovers()
        # Take 를 끝내는 쪽(stop · 자동 종료 · 끝내기 PUT)은 따로 기다린다. 스트림만 닫힌 경우를
        # 위해 남은 것을 넣어 둔다 — 아무도 다시 붙지 않아 떠난 것으로 끝나도 원자료는 남긴다
        self._record.metrics.flush_soon()
        self.state = "closed"
        self._closed.set()
        if self._record.coach.notify == self._notify_coach:
            # 끝난 스트림을 Take 기록이 붙잡지 않게 (기록은 프로세스에 오래 남는다)
            self._record.coach.notify = None
        # 레지스트리에서 빠진다. Take 는 끝나지 않고 스트림만 닫혔으면(grace 만료 등) 아무도 다시
        # 붙지 않을 때 끝낸다 — 창을 닫았으면(새로고침이면 그 안에 다시 붙는다) 짧게 기다린다
        self._host.stream_closed(
            self, self._store, discarded=self._discarded, tab_closed=self._tab_closed
        )

    # ── 1초 원자료 ────────────────────────────────────────────────────

    def push_metrics(self, message: MetricsMessage, *, source: WebSocket | None = None) -> None:
        """FE 의 1초 측정값 하나. 그 1초의 STT 상태(`_stt_status_of`)와 함께 원자료로 모은다.

        쫓겨난 연결이 보낸 것은 버린다 (오디오와 같은 규칙). 스트림이 닫혀도 연결이 붙어 있으면
        받는다 — 자동 종료를 알린 뒤 FE 가 보내는 마지막 1초 조각은 Deepgram 정리가 끝난 뒤에 올 수
        있다. Take 를 끝낸 뒤에 온 것은 버퍼가 버린다 (`MetricBuffer.seal`).
        이미 받은 1초와 Take 의 상한을 넘은 것은 저장하지도 코치에 넘기지도 않는다
        (`MetricBuffer.admit`).
        """
        if self._discarded:
            return
        if source is not None and not self._client.holds(source):
            return
        if not self._record.metrics.admit(message.t_ms):
            return
        row = MetricSecondCreateDTO(
            t_ms=message.t_ms,
            slide_number=message.slide_number,
            gaze=message.gaze,
            voice=message.voice,
            stt_status=self._stt_status_of(message),
        )
        self._record.metrics.add(row)
        self._record.coach.on_metrics(
            message.t_ms,
            gaze=message.gaze,
            voice=message.voice,
            stt_status=row.stt_status,
            buffered=message.buffered,
        )

    def push_slide(self, message: SlideMessage, *, source: WebSocket | None = None) -> None:
        """FE 가 장을 넘겼다. 원자료로 저장하고 코치의 지금 장을 고친다 (1초 원자료와 같은 규칙)."""
        if self._discarded:
            return
        if source is not None and not self._client.holds(source):
            return
        event = SlideEvent(message.slide_number, message.t_ms)
        if not self._record.coach.slide_room(event):
            if not self._slides_capped:
                self._slides_capped = True
                logger.warning(
                    "장 변경이 상한을 넘어 버린다 take=%s limit=%d", self.take_id, MAX_SLIDE_CHANGES
                )
            return
        self._record.slides.add(event)
        self._record.coach.on_slide(event)

    def _stt_status_of(self, message: MetricsMessage) -> SttState:
        """그 1초 동안의 STT 상태. 받았을 때의 상태를 쓰되, `ok` 는 그 1초의 오디오가 끊김 없이
        들어왔을 때만이다.

        FE 는 끊긴 동안 쌓아 둔 1초를 다시 붙은 뒤 보낸다. 받았을 때의 상태만 적으면 STT 가 듣지
        못한 구간이 `ok` 로 남아, 복구(`replay`)가 그 구간을 '말이 없었다' 로 센다. 그런 1초는
        오디오가 멈춘 것과 같게 `degraded` 로 적는다 (코치가 오디오가 멈춘 1초를 STT 모듈에 넘기는
        규칙과 같다).
        무음으로 메운 짧은 끊김(`MAX_SILENCE_FILL_MS` 이하)은 이어진 것으로 본다.
        """
        # 자동 종료 뒤의 마지막 1초 조각은 스트림이 정리된 뒤에 온다. 정리 전 상태로 본다 —
        # `closed` 로 적으면 복구가 그 1초의 단어를 세지 않는다
        state = self._open_state if self.state == "closed" else self.state
        if state != "ok":
            return state
        starts = [r["t_ms"] for r in (message.gaze, message.voice) if r is not None]
        start_ms = min(starts) if starts else message.t_ms - METRICS_PERIOD_MS
        return "ok" if self._heard.covers(start_ms, message.t_ms) else "degraded"

    # ── 오디오 투입 ───────────────────────────────────────────────────

    def push(self, data: bytes, *, source: WebSocket | None = None) -> None:
        """FE 프레임 하나. InvalidAudioFrame 은 호출자가 처리한다.

        `source` 는 프레임을 보낸 연결. 다른 탭이 이어받아 쫓겨난 연결이 닫히기 전에 보낸
        프레임은 버린다 — 섞이면 새 연결의 seq 를 앞질러 그쪽 오디오가 역행으로 버려진다.
        Deepgram 에 보낼 길이가 서버 시간을 앞지른 프레임도 버린다 (`AudioBudget`).
        """
        frame = parse_audio_frame(data)
        if self._stopping:
            # stop 신호 뒤의 오디오는 아무도 소비하지 않는다. 큐에 넣으면 통계만 더럽힌다
            return
        if source is not None and not self._client.holds(source):
            return
        now = time.monotonic()
        self._last_audio_at = now
        accepted = self.sequencer.accept(
            frame, allow=lambda cost_ms: self._budget.take(cost_ms, now * 1000)
        )
        if accepted is None:
            if self.sequencer.over_budget and not self._over_budget_logged:
                self._over_budget_logged = True
                logger.warning(
                    "오디오가 서버 시간보다 빨리 와 프레임을 버린다 take=%s seq=%d offset_ms=%d",
                    self.take_id,
                    frame.seq,
                    frame.offset_ms,
                )
            return
        self._heard.add(accepted)
        if accepted.restart:
            logger.info(
                "새 연결이 seq 를 처음부터 보낸다(탭 새로고침). 타임라인을 다시 잡는다 "
                "take=%s seq=%d offset_ms=%d",
                self.take_id,
                accepted.frame.seq,
                accepted.frame.offset_ms,
            )
        self._audio.put(accepted)
        # 큐에 넣은 뒤에 본다. 먼저 끝내면 stop 신호 뒤에 들어가 이 프레임이 버려진다
        if self._time_limit.reached(accepted.frame.end_ms):
            self._auto_end(AutoEndReason.TIME_LIMIT)

    def _drop(self, item: Accepted) -> None:
        lost = item.silence_ms + item.frame.duration_ms
        self.dropped_audio_ms += lost
        self._stt.on_dropped(lost, timeline_break=item.timeline_break)
        # STT 에 닿지 못한 오디오다. 그 뒤부터를 끊김 없이 들은 것으로 본다 (`_stt_status_of`)
        self._heard.drop_until(item.frame.end_ms)

    # ── Deepgram 세션 루프 ────────────────────────────────────────────

    async def _run(self) -> None:
        """Deepgram 세션을 필요한 만큼 이어서 연다 (`SttLoop.run`). 끝나면 정리한다."""
        try:
            await self._stt.run()
            # 정상으로 끝났다. 저장 못 한 final 을 한 번 더 저장해 본다. Take 종료는 이 스트림이
            # 닫힌 뒤에 적으므로(`RealtimeSession._stop`) 종료 전에 들어간다
            if not await self._transcripts.save_unsaved():
                self.discard()
        finally:
            self._finish()

    async def _on_transcript(self, event: Transcript) -> None:
        base = self._stt.base_offset_ms or 0
        if event.transcript or event.words:
            message = TranscriptMessage(
                segment_id=self._transcripts.segment_id(self.stt_session_no),
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
            if not event.is_final:
                await self._client.send(message)
            else:
                # 확정 시각은 이 결과를 받기 직전의 마지막 metrics 시각이다 (coach-agent.md 12-1).
                # 화면에 보내는 await 보다 먼저 매긴다 — 그동안 온 metrics 의 요청에는 이 단어가
                # 실리지 않았으므로 그 시각을 확정 시각으로 적으면 replay 와 어긋난다
                final_at_ms = self._record.coach.on_final(
                    (w.word, w.start_ms, w.end_ms) for w in message.words
                )
                # 받은 final 은 정리(취소)가 와도 끝까지 처리한다. 중간에 끊기면 화면에도 DB 에도
                # 남지 않는다
                with anyio.CancelScope(shield=True):
                    await self._show_final(message)
                    # 화면에 먼저 보내고 저장한다. 저장은 클라이언트가 붙어 있든 없든 한다 —
                    # 리포트의 원천은 이 행이지 화면이 아니다
                    if not await self._transcripts.persist(
                        message, final_at_ms, self.stt_session_no
                    ):
                        # Take 가 지워졌다. 저장할 곳이 없으니 더 돌 이유가 없다
                        self.discard()
        if event.is_final:
            # 빈 final 도 구간을 닫는다. 다음 interim 은 새 번호로
            self._transcripts.next_segment()
            # 빈 final 도 센다 — 말 없이 들은 구간이라는 뜻이다
            self._observe_silence(event)

    async def _show_final(self, message: TranscriptMessage) -> None:
        """final 을 화면에 보낸다. 못 보냈으면 모아 두었다가 다시 붙은 연결에 보낸다.

        FE 가 떨어져 있을 때만이 아니라 보내다 실패하거나 상한에 걸렸을 때도 모은다 — 보낸 셈 치면
        다시 붙은 화면에서 그 구간이 빈다. 전송은 상대가 멈춰 있을 수 있어 상한을 둔다. 늦었을 뿐
        실제로는 닿았으면 같은 `segment_id` 로 한 번 더 가고 FE 는 덮어쓴다.
        interim 은 다음 것이 덮으니 모으지 않는다.
        """
        target = self._client.ws
        if target is not None and await self._send_final_to(message, target):
            return
        self._transcripts.hold_missed(message)
        current = self._client.ws
        if current is not None and current is not target and current is self._caught_up:
            # 보내는 동안 다른 탭이 이어받았고 그 연결은 놓친 final 을 이미 받았다. 다음 재연결까지
            # 기다리지 않고 지금 보낸다. 아직 ready 전인 연결이면 그 연결이 따라잡을 때 같이 간다
            with anyio.move_on_after(stt_loop.ABORT_TIMEOUT_SEC):
                await self.resend_missed()

    async def _send_final_to(self, message: TranscriptMessage, target: WebSocket) -> bool:
        # ready를 받은 연결 하나에 고정한다. 보내는 동안 이어받기가 일어나면 옛 연결로 성공한
        # 전송도 현재 화면에는 닿지 않은 것이므로 목록에서 지우지 않는다.
        if not self._client.holds(target):
            return False
        sent = False
        with anyio.move_on_after(stt_loop.ABORT_TIMEOUT_SEC):
            sent = await self._client.send_checked(message)
        return sent and self._client.holds(target)

    def _observe_silence(self, event: Transcript) -> None:
        try:
            silent = self._record.silence.observe(
                event.start_ms,
                event.end_ms,
                ((w.start_ms, w.end_ms) for w in event.words),
                has_text=bool(event.transcript.strip()),
                unheard=self._stt.filled_ms,
            )
            # final 은 시각 순으로 온다. 이 final 앞에서 끝난 메운 구간은 다시 볼 일이 없다
            self._stt.forget_filled(event.end_ms)
        except Exception:
            # 자동 종료는 부가 기능이다. 여기서 예외가 새면 세션이 _LOST 로 처리돼 재접속한다
            logger.exception("말 없음 판단 실패 take=%s", self.take_id)
            return
        if silent:
            self._auto_end(AutoEndReason.SILENCE)

    # ── 자동 종료 ─────────────────────────────────────────────────────

    def _auto_end(self, reason: AutoEndReason) -> None:
        """Take 를 끝낸다. 이미 정리 중이면(사용자가 stop 을 보냈거나 먼저 걸린 조건) 무시한다.

        오디오는 바로 끊는다 — 이 뒤로 들어오는 프레임은 `push` 가 버린다. 나머지(FE 알림·DB·
        연결 닫기)는 기다려야 하는 일이라 따로 태스크로 돈다. 이 메서드는 이벤트 펌프와
        타이머에서 불리는데, 거기서 정리를 기다리면 정리가 그 펌프를 기다려 서로 멈춘다.
        """
        if self._stopping:
            return
        self.auto_end_reason = reason
        # DB 에 적히기 전에 다시 붙으려는 연결도 막는다
        self._host.take_ended(self.take_id, reason)
        self._record.coach.stop()
        if self._grace_task is not None:
            # 떨어져 있는 동안 끝났다. 기다릴 재연결이 없다
            self._grace_task.cancel()
            self._grace_task = None
        logger.info(
            "Take 자동 종료 take=%s reason=%s silent_run_ms=%d frames=%d",
            self.take_id,
            reason,
            self._record.silence.silent_run_ms,
            self.sequencer.frames,
        )
        self.request_stop()
        self._host.spawn(self._finish_auto_end(reason), name=f"take-auto-end:{self.take_id}")

    async def _finish_auto_end(self, reason: AutoEndReason) -> None:
        """사용자가 stop 을 보냈을 때(`RealtimeSession._stop`)와 같은 순서로 정리한다.

        FE 가 그 사이 stop 을 보내도 괜찮다 — 양쪽 다 같은 스트림이 닫히기를 기다리고, 두 번
        보내는 closed 와 close 는 FE 가 이미 닫은 뒤라 조용히 버려진다.
        """
        with anyio.move_on_after(CONTROL_SEND_TIMEOUT_SEC):
            await self._client.send(
                AutoEndMessage(reason=reason, message=self.auto_end_policy.message(reason))
            )
        if not await self.wait_closed(stt_loop.DRAIN_TIMEOUT_SEC + stt_loop.ABORT_TIMEOUT_SEC):
            logger.warning("자동 종료 drain 타임아웃 take=%s. 스트림을 끊는다", self.take_id)
            self.cancel()
            await self.wait_cancelled(stt_loop.ABORT_TIMEOUT_SEC)
        if self._client.connected:
            # FE 는 auto_end 를 받고 마지막 1초 조각을 보낸 뒤 stop 을 보낸다. Deepgram 정리가
            # 먼저 끝났으면 그 조각이 아직 안 왔을 수 있다 — stop 이 오거나 연결이 떠날 때까지
            # 잠깐 기다린다
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._client.done.wait(), FINAL_METRICS_WAIT_SEC)

        # 마지막 확정 전사와 1초 원자료를 저장한 뒤에 끝난 것으로 적는다 (`RealtimeSession._stop` 과
        # 같은 이유). 못 적어도 정리는 계속한다 — 적기는 뒤에서 다시 하고, 그동안 이 프로세스
        # 안에서는 registry 의 끝낸 Take 기록이 재연결을 막는다.
        # 그 사이 Take 가 지워졌거나 이미 끝났으면(FE 의 stop 이 먼저 적은 것 포함) 적지 않는다
        await self._host.finish_take(
            self.take_id, self._store, end_reason=reason.value, coach=self._coach
        )

        client = self._client.ws
        if client is None:
            # FE 가 떨어져 있다. 다시 붙으면 TAKE_ENDED 로 이유를 받는다
            return
        await self.send_status()
        await quiet(client.close(code=1000))

    # ── FE 로 ─────────────────────────────────────────────────────────

    async def _set_state(self, state: SttState) -> None:
        if self.state == state:
            return
        self.state = state
        self._open_state = state
        await self.send_status()

    async def send_status(self) -> None:
        # 상태 전송은 Deepgram 세션을 연 직후에도 한다. FE 가 받지 않아도 STT 에 진입하고,
        # stop 을 처리해 세션을 닫을 수 있어야 한다
        with anyio.move_on_after(CONTROL_SEND_TIMEOUT_SEC):
            await self._client.send(
                SttStatusMessage(
                    state=self.state,
                    stt_session_no=self.stt_session_no,
                    frames=self.sequencer.frames,
                    dropped_frames=self.sequencer.dropped,
                    silence_ms=self.sequencer.silence_ms,
                    lost_ms=self.sequencer.lost_ms + self.dropped_audio_ms,
                )
            )

    async def _notify_coach(self, t_ms: int, reply: EvaluateReply) -> None:
        """코치의 한마디 · 상태 표시를 FE 에 보낸다 (WS `coach`). 떨어져 있으면 버린다."""
        await self._client.send(
            CoachMessage(
                t_ms=t_ms,
                action=reply.action,
                feedback=reply.feedback.model_dump() if reply.feedback is not None else None,
                indicators=reply.indicators,
            )
        )
