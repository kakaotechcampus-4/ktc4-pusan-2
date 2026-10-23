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
- **WS-2** (BE↔Deepgram): 큐에 담아 두고 백오프로 재접속한다. 새 세션은 타임스탬프가 다시
  0 부터라 `base_offset_ms` 를 새로 잡고 `stt_session_no` 를 올린다. 재접속이 이어서 실패해도
  **연결을 끊지 않는다** — FE 1단 코치는 계속 돌아야 하므로 `degraded` 만 알리고 재시도한다.

타임라인 규칙: 한 Deepgram 세션 안에서는 `take_ms = base_offset_ms + deepgram_ms` 가 성립해야
한다. 그래서 프레임 사이 갭은 무음으로 메운다. 메우기에 너무 긴 갭(`MAX_SILENCE_FILL_MS` 초과)
은 **세션을 갈아** base 를 다시 잡는다 — 일부만 메우면 그 뒤 전사 시각이 통째로 앞당겨진다.

final 은 `TranscriptStore` 로 저장한다 (take 모듈 service 에 위임). 스트림이 새로 만들어질 때는
저장된 마지막 번호를 이어 받는다 — grace 가 만료돼 스트림이 사라진 뒤 다시 붙어도 `segment_id`
가 `"1-1"` 부터 다시 나오지 않아야 FE 가 앞부분을 덮어쓰지 않고, `UNIQUE(take_id, seq)` 도 지켜진다.
저장에 실패한 final 은 들고 있다가 다음 final 을 저장할 때와 스트림이 정상으로 끝날 때 다시
저장한다.

스트림 정리와 Take 종료는 다르다. 사용자가 stop 을 보내면(`end`) Take 가 끝나 같은 Take 로 다시
붙지 못한다. grace 만료 · 오디오 없는 연결 · 앱 종료는 스트림만 정리한다 — FE 는 끊긴 뒤에도
다시 붙고, 새 스트림이 번호를 이어 받는다. 그렇게 정리된 뒤 `ABANDON_AFTER_SEC` 동안 아무도
다시 붙지 않으면 사용자가 떠난 것으로 보고 Take 를 `FAILED`(`ABANDONED`)로 끝낸다(`arm_abandon`).
이 타이머는 메모리에만 있어 앱이 다시 뜨면 DB 를 보고 다시 건다(`arm_abandoned_takes`).

자동 종료(`auto_end.py`): 말 없음이나 최대 시간에 걸리면 사용자가 stop 을 보낸 것과 같은 순서로
정리한다 — `auto_end` 를 알리고, 남은 오디오를 흘리고, `stt_status` closed 를 보내고 1000 으로
닫는다. Take 를 `ANALYZING` 으로 바꾸고 이유를 DB 에 남겨 같은 Take 로 다시 붙지 못하게 한다.
말 없음 기록과 서버 시계는 스트림이 아니라 Take 에 붙어 있어(`_TakeRecord`) 스트림이 새로 열려도
이어진다.

MVP 는 uvicorn 1 프로세스라 레지스트리가 in-process dict 다. 워커를 늘리려면 sticky 라우팅이나
Redis 가 필요한데 그때는 어차피 재설계다 (계획 문서 §8-6).
"""

import asyncio
import contextlib
import logging
import time
import uuid
from collections import OrderedDict
from collections.abc import Awaitable, Coroutine, Iterable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime

import anyio
from fastapi import WebSocket
from pydantic import BaseModel
from websockets.exceptions import ConnectionClosed

from pitch_coach_backend.module.take.dto import TranscriptSegmentCreateDTO, TranscriptWordDTO
from pitch_coach_backend.module.take.exception import DuplicateTranscriptSegment, NonExistentTake
from pitch_coach_backend.realtime.audio import BYTES_PER_MS, silence
from pitch_coach_backend.realtime.auto_end import AutoEndPolicy, SilenceTracker
from pitch_coach_backend.realtime.dto import (
    AutoEndMessage,
    AutoEndReason,
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
    AudioFrame,
    FrameSequencer,
    parse_audio_frame,
)
from pitch_coach_backend.realtime.stt_adapter import (
    Metadata,
    SttAdapter,
    SttConfig,
    SttConfigRejected,
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
# Deepgram 이 keyterm 을 거절할 때마다 뒤에서 빼는 개수. 몇 토큰 넘쳤는지 알려 주지 않아서,
# 한 번에 반씩 버리는 대신 조금씩 빼 우선순위 높은 용어를 최대한 남긴다
KEYTERM_TRIM_STEP = 5
# CloseStream 뒤 남은 결과와 Metadata 를 기다리는 상한
DRAIN_TIMEOUT_SEC = 10.0
# 취소·오류로 세션을 버릴 때 Deepgram 소켓 닫기(abort) 를 기다리는 상한. 이게 없으면
# 상대가 close 핸드셰이크에 답하지 않을 때 shield 안에서 영영 기다린다
ABORT_TIMEOUT_SEC = 3.0
# Deepgram 이 받았다는 길이와 우리가 보낸 길이의 허용 오차
DURATION_TOLERANCE_MS = 50
# FE 가 떨어져 있는 동안 모아 둘 final 개수. 10분 발표가 200개 안팎이다
MAX_MISSED_FINALS = 200
# 저장에 실패해 들고 있을 final 개수. 넘치면 오래된 것부터 버린다
MAX_UNSAVED_FINALS = 200
# 자동 종료를 DB 에 적는 상한. DB 가 늦어도 연결 정리까지 붙잡히지 않게 한다
MARK_AUTO_END_TIMEOUT_SEC = 5.0
# 연결 없이 스트림이 정리된 뒤 이만큼 아무도 다시 붙지 않으면 사용자가 떠난 것으로 보고 Take 를
# 끝낸다. FE 가 끊긴 뒤 다시 붙어 보는 시간(sttSocket `GIVE_UP_AFTER_MS`)과 같다 — 그 안이면 탭이
# 살아 있는 FE 도, 탭을 닫았다가 리허설 주소를 다시 연 사용자도 이어 갈 수 있다
ABANDON_AFTER_SEC = 30 * 60.0
# 앱이 뜰 때 진행 중인 Take 를 읽는 상한. 이걸 기다리는 동안 앱이 요청을 받지 못한다
ARM_ABANDONED_TIMEOUT_SEC = 10.0
# 끝낸 Take · Take 별 자동 종료 상태를 프로세스 안에서 기억하는 개수. 오래된 것부터 버린다
MAX_REMEMBERED_TAKES = 10_000

# _run_session 이 끝난 이유
_STOPPED = "stopped"  # CloseStream 을 보냈다. Take 가 끝났다
_ROTATE = "rotate"  # 타임라인이 끊겨 새 세션이 필요하다
_LOST = "lost"  # Deepgram 이 끊었다


class StreamOwnerMismatch(Exception):
    """다른 사용자가 이미 쓰고 있는 Take 다."""


class TakeEnded(Exception):
    """이 프로세스에서 끝낸 Take 다. DB 에 적히기 전이라도 다시 붙지 못한다.

    BE 가 자동으로 끝냈으면 `reason` 에 이유가 있다. 사용자가 끝냈으면 None.
    """

    def __init__(self, reason: AutoEndReason | None = None) -> None:
        super().__init__(reason)
        self.reason = reason


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
        auto_end: AutoEndPolicy | None = None,
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
        self.auto_end_policy = AutoEndPolicy.from_settings() if auto_end is None else auto_end

        # ── 연결이 바뀌어도 이어지는 상태 ──
        self.sequencer = FrameSequencer()
        # 둘 다 Take 기준이다. 스트림이 새로 만들어질 때 저장된 마지막 값에서 이어 받는다
        self.stt_session_no = stt_session_no
        self.segment_no = segment_no
        self.state: SttState = "connecting"
        # 큐가 넘치거나 Deepgram 이 죽어 있어 STT 에 닿지 못한 오디오
        self.dropped_audio_ms = 0
        # 말 없음 기록 · 서버 시계. Take 에 붙어 있어 이 스트림보다 오래 산다
        self._record = _take_record(take_id, self.auto_end_policy)
        # 이 스트림을 연 시각(monotonic). 닫힐 때 열려 있던 시간을 Take 의 서버 시계에 더한다
        self._opened_at: float | None = None
        # 최대 시간 타이머. start() 에서 건다
        self._time_limit: asyncio.TimerHandle | None = None

        # ── Deepgram 세션마다 리셋 ──
        self._base_offset_ms: int | None = None
        self._bytes_sent = 0
        # 이 세션에 BE 가 메워 보낸 무음 구간 (Deepgram 세션 시각 ms). 들은 오디오가 아니라
        # 말 없음에서 뺀다. 그 구간을 덮는 final 을 본 뒤에는 버린다
        self._filled_ms: list[tuple[int, int]] = []
        # 드롭된 오디오만큼 다음 프레임 앞에 채울 무음. 타임라인을 밀리지 않게 한다
        self._pending_silence_ms = 0
        # 드롭된 프레임이 타임라인 단절(`Accepted.timeline_break`)이었다. 그 프레임과 함께 사라지면
        # 세션을 갈지 않고 이어 붙여 전사 시각이 어긋난다 — 다음 프레임이 대신 세션을 간다
        self._pending_break = False
        # 세션을 갈면서 넘긴 프레임. 새 세션의 첫 프레임이 된다
        self._carry: Accepted | None = None

        self._queue: asyncio.Queue[Accepted | None] = asyncio.Queue(maxsize=QUEUE_MAX_FRAMES)
        self._client: WebSocket | None = None
        # FE 가 떨어져 있는 동안 온 final. 다시 붙으면 순서대로 보낸다
        self._missed_finals: list[TranscriptMessage] = []
        # 저장에 실패한 final. 번호 순서대로 다시 저장한다 (`_save_unsaved`)
        self._unsaved: list[TranscriptSegmentCreateDTO] = []
        # resend_missed() 를 감싼다. 탭 두 개가 거의 동시에 붙으면(예: 재연결이 겹칠 때)
        # 서로 다른 RealtimeSession 이 이 메서드를 동시에 부를 수 있다. 락 없이 같은
        # 리스트를 각자 확인(while)->접근([0])->제거(pop) 하면, await 로 갈라진 그 틈에
        # 한쪽이 이미 비운 자리를 다른 쪽이 또 pop(0) 하다가 IndexError 가 난다
        self._resend_lock = asyncio.Lock()
        self._grace_task: asyncio.Task[None] | None = None
        self._task: asyncio.Task[None] | None = None
        # cancel() 뒤 _run 의 정리를 기다리는 태스크. 참조를 들고 있어야 한다 — asyncio 는
        # 태스크를 약하게만 잡고 있어서, 아무도 참조하지 않는 태스크는 GC 에 끝나기 전에
        # 사라질 수 있다 (asyncio.create_task 문서의 경고)
        self._reaper: asyncio.Task[None] | None = None
        # Take 가 지워져 끊었다 (`discard`). 붙어 있던 연결에 알리는 태스크도 위와 같은 이유로 든다
        self._discarded = False
        self._closer: asyncio.Task[None] | None = None
        self._ever_connected = False
        self._stopping = False
        # BE 가 자동으로 끝냈으면 그 이유. 사용자가 그 뒤에 보낸 stop 도 같은 이유로 적는다
        self.auto_end_reason: AutoEndReason | None = None
        self._stop_event = asyncio.Event()
        self._closed = asyncio.Event()

    @property
    def is_stopping(self) -> bool:
        """정리에 들어갔다. 새 연결을 붙이면 안 된다 — 곧 사라질 스트림이다."""
        return self._stopping

    @property
    def is_finished(self) -> bool:
        return self._closed.is_set()

    @property
    def has_client(self) -> bool:
        """연결이 붙어 있다. 끊긴 뒤 다시 붙기를 기다리는 동안은 False."""
        return self._client is not None

    # ── 수명 ──────────────────────────────────────────────────────────

    def start(self) -> None:
        """백그라운드 태스크로 띄운다. WebSocket 요청 스코프에 묶지 않는다."""
        # 다시 붙었다. 떠난 것으로 보고 끝내려던 타이머를 끈다
        _cancel_abandon(self.take_id)
        self._task = asyncio.create_task(self._run(), name=f"take-stream:{self.take_id}")
        self._opened_at = time.monotonic()
        self._arm_time_limit()

    def _arm_time_limit(self) -> None:
        """최대 시간의 보조 상한. 기준은 프레임의 Take 시각(`_check_time_limit`)이고, 이건 그 시각이
        가지 않는 Take(오디오 없이 연결만 붙여 둠, offset 이 멈춤)를 막는다.

        - 스트림을 열 때 건다. 첫 프레임에서 걸면 오디오를 안 보내고 연결만 붙여 둔 Take 는
          영영 안 끝나고 Deepgram 연결도 계속 살아 있다.
        - 재는 것은 이 Take 의 스트림이 **열려 있던 시간의 합**이다. 스트림 하나 기준이면 grace
          (30초)보다 오래 끊었다 다시 붙을 때마다 1시간이 새로 시작하고, 벽시계 기준이면 탭이 죽어
          있던 시간까지 세어 이어서 연습하자마자 끝난다. FE 무대에는 일시정지가 없어서 열려 있던
          시간은 무대 시계와 거의 같다 — 조금 빠른 만큼(연결 · grace) 여유를 둔다.
        """
        policy = self.auto_end_policy
        limit_sec = (policy.max_duration_ms + policy.server_clock_slack_ms) / 1000
        remaining_sec = limit_sec - self._record.streamed_sec
        self._time_limit = asyncio.get_running_loop().call_later(
            max(0.0, remaining_sec), self._auto_end, AutoEndReason.TIME_LIMIT
        )

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
        self.sequencer.new_connection()
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

        `ready` 뒤에 부른다. 저장은 화면과 따로 하므로 리포트에는 영향이 없다 —
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

    def is_client(self, ws: WebSocket) -> bool:
        """지금 붙어 있는 연결인가. 다른 탭이 이어받아 쫓겨난 연결이면 False."""
        return self._client is ws

    def detach(self, ws: WebSocket) -> None:
        """연결이 끊겼다. 이미 다른 연결이 붙었으면 아무것도 하지 않는다."""
        if self._client is not ws:
            return
        self._client = None
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
        self._put(None)

    def end(self) -> None:
        """사용자가 Take 를 끝냈다. 스트림을 정리하고, 같은 Take 로 다시 붙지 못하게 한다.

        `request_stop` 만으로는 Take 가 끝나지 않는다 — grace 만료나 오디오 없는 연결도 스트림을
        정리하지만, 그 뒤 FE 가 다시 붙으면 새 스트림으로 이어 가야 한다.
        이미 자동으로 끝낸 뒤 온 stop(FE 가 `auto_end` 를 받고 보낸 것)이면 그 이유를 지우지 않는다.
        """
        if self.take_id not in _ended:
            _ended[self.take_id] = None
        self.request_stop()

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

        스트림 태스크 안(`_persist`)에서도 부른다. 연결을 닫는 일을 따로 태스크로 돌리는 이유다 —
        여기서 기다리면 `cancel()` 이 그 자리를 끊어 알림이 안 나간다.
        """
        if self._discarded:
            return
        self._discarded = True
        client = self._client
        # 끊은 스트림의 전사·상태가 이 연결로 더 나가지 않게 먼저 뗀다
        self._client = None
        self.cancel()
        if client is not None:
            self._closer = asyncio.create_task(
                self._close_deleted_client(client), name=f"take-stream-discard:{self.take_id}"
            )

    async def _close_deleted_client(self, client: WebSocket) -> None:
        await _quiet(
            client.send_text(
                ErrorMessage(
                    code=WsErrorCode.TAKE_NOT_FOUND, message="연습을 찾을 수 없습니다."
                ).model_dump_json()
            )
        )
        await _quiet(client.close(code=1008, reason=WsErrorCode.TAKE_NOT_FOUND))

    def _finish(self) -> None:
        if self._closed.is_set():
            return
        if self._time_limit is not None:
            self._time_limit.cancel()
            self._time_limit = None
        if self._opened_at is not None:
            self._record.streamed_sec += time.monotonic() - self._opened_at
            self._opened_at = None
        # Deepgram 에 닿지 못한 채 큐에 남은 오디오는 유실이다. 마지막 stt_status 의
        # lost_ms 가 이걸 빼먹으면 리포트가 "STT 다 받았다" 고 잘못 말한다
        while True:
            try:
                item = self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            if item is not None:
                self._drop(item)
        if self._missed_finals:
            # 저장은 화면과 따로 한다 (final 은 붙어 있는 클라이언트와 무관하게 저장한다).
            # 실시간 화면에만 못 간 것이라 개수만 남긴다
            logger.info(
                "재연결 없이 끝나 화면에 못 보낸 final take=%s count=%d",
                self.take_id,
                len(self._missed_finals),
            )
        if self._unsaved:
            # 끝날 때까지 저장하지 못했다. 이 구간은 리포트에서 빠진다
            logger.error(
                "저장하지 못한 final 을 버린다 take=%s count=%d seq=%d~%d",
                self.take_id,
                len(self._unsaved),
                self._unsaved[0].seq,
                self._unsaved[-1].seq,
            )
        self.state = "closed"
        self._closed.set()
        forget(self)
        if not self._discarded and self.take_id not in _ended:
            # Take 는 끝나지 않고 스트림만 닫혔다 (grace 만료 등). 아무도 다시 붙지 않으면 끝낸다
            arm_abandon(self.take_id, self._store, closed_at=datetime.now(UTC))

    # ── 오디오 투입 ───────────────────────────────────────────────────

    def push(self, data: bytes, *, source: WebSocket | None = None) -> None:
        """FE 프레임 하나. InvalidAudioFrame 은 호출자가 처리한다.

        `source` 는 프레임을 보낸 연결. 다른 탭이 이어받아 쫓겨난 연결이 닫히기 전에 보낸
        프레임은 버린다 — 섞이면 새 연결의 seq 를 앞질러 그쪽 오디오가 역행으로 버려진다.
        """
        frame = parse_audio_frame(data)
        if self._stopping:
            # stop 신호 뒤의 오디오는 아무도 소비하지 않는다. 큐에 넣으면 통계만 더럽힌다
            return
        if source is not None and source is not self._client:
            return
        accepted = self.sequencer.accept(frame)
        if accepted is None:
            return
        if accepted.restart:
            logger.info(
                "새 연결이 seq 를 처음부터 보낸다(탭 새로고침). 타임라인을 다시 잡는다 "
                "take=%s seq=%d offset_ms=%d",
                self.take_id,
                accepted.frame.seq,
                accepted.frame.offset_ms,
            )
        self._put(accepted)
        # 큐에 넣은 뒤에 본다. 먼저 끝내면 stop 신호 뒤에 들어가 이 프레임이 버려진다
        self._check_time_limit(accepted.frame)

    def _check_time_limit(self, frame: AudioFrame) -> None:
        """프레임의 Take 시각(offset)으로 최대 시간을 본다. 최대 시간의 기준이다 — FE 무대 시계라
        새로고침 · 재연결에도 이어지고, 프로세스가 다시 떠 서버 시계를 잃어도 여기서 걸린다.
        서버 시계(`_arm_time_limit`)는 이 시각이 가지 않을 때만 대신 끝낸다."""
        if frame.end_ms >= self.auto_end_policy.max_duration_ms:
            self._auto_end(AutoEndReason.TIME_LIMIT)

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
        if item.timeline_break:
            self._pending_break = True

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
                self._filled_ms = []
                if self._carry is None:
                    # 넘겨받은 프레임이 있으면 지우지 않는다. 세션을 가는 동안 드롭된 것은 그
                    # 프레임 **뒤**의 오디오라 새 세션에서 무음으로 메워야 한다 (_send_frame)
                    self._pending_silence_ms = 0
                await self._set_state("ok")

                try:
                    reason = await self._run_session(session)
                except Exception:
                    # 어댑터·파서의 버그처럼 예상 못 한 예외다. 여기서 태스크가 죽으면 FE 는
                    # 아무 알림 없이 STT 만 조용히 멈춘 상태가 된다 (큐는 쌓이고 stt_status
                    # 는 안 온다). Deepgram 이 끊긴 것과 같게 다뤄 재접속한다
                    logger.exception("STT 세션에서 예상 못 한 예외 take=%s", self.take_id)
                    reason = _LOST
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
            # 정상으로 끝났다. 저장 못 한 final 을 한 번 더 저장해 본다. Take 종료는 이 스트림이
            # 닫힌 뒤에 적으므로(`RealtimeSession._stop`) 종료 전에 들어간다
            await self._save_unsaved()
        finally:
            self._finish()

    async def _connect(self) -> SttSession | None:
        while True:
            try:
                return await self._adapter.connect(self._config)
            except SttConfigRejected as e:
                keyterms = self._config.keyterms
                floor = self._config.min_keyterms
                if len(keyterms) <= floor:
                    logger.error("Deepgram 이 설정을 거절했다 take=%s %s", self.take_id, e)
                    return None
                # 같은 설정으로 다시 붙으면 또 거절된다. 우선순위가 낮은 뒤쪽 용어부터 빼고
                # 기다리지 않고 바로 다시 붙는다 — 실패로 세서 backoff 하면 몇 번만 줄여도
                # 수십 초 동안 전사가 안 나온다. 줄인 설정은 이후 재접속에도 그대로 쓴다
                trimmed = keyterms[: max(floor, len(keyterms) - KEYTERM_TRIM_STEP)]
                logger.warning(
                    "Deepgram 이 keyterm 을 거절해 %d개 → %d개로 줄인다 take=%s %s",
                    len(keyterms),
                    len(trimmed),
                    self.take_id,
                    e,
                )
                self._config = replace(self._config, keyterms=trimmed)
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
                        # Deepgram 이 닫았다. 이벤트 쪽은 끊지 않는다 — 이미 받아 둔 final 을
                        # 마저 읽고, 닫힌 소켓이라 읽을 것이 떨어지면 스스로 끝난다
                        reason = _LOST
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
            # 다시 취소되어 연결이 그대로 남는다. 다만 shield 안이라 바깥에서는 끊을 수
            # 없으므로 상한을 둔다 — 상대가 close 에 답하지 않으면 그냥 버리고 나온다
            with anyio.CancelScope(shield=True), anyio.move_on_after(ABORT_TIMEOUT_SEC) as scope:
                await _quiet(session.abort())
            if scope.cancelled_caught:
                logger.warning("Deepgram 소켓 닫기 타임아웃 take=%s. 버리고 나온다", self.take_id)
        return reason

    async def _pump_audio(self, session: SttSession) -> str:
        while True:
            carried = self._carry is not None
            item = self._carry or await self._queue.get()
            self._carry = None
            if item is None:
                await session.close_stream()
                return _STOPPED

            gap_ms = item.silence_ms + self._pending_silence_ms
            if self._base_offset_ms is not None and (
                item.timeline_break or self._pending_break or gap_ms > MAX_SILENCE_FILL_MS
            ):
                # 무음으로 메우기엔 너무 긴 갭이다. 이대로 보내면 이후 전사 시각이
                # 갭만큼 앞당겨진다. 세션을 갈아 base 를 다시 잡는다
                logger.info(
                    "타임라인 단절로 Deepgram 세션을 교체한다 take=%s gap_ms=%d",
                    self.take_id,
                    gap_ms + item.lost_ms,
                )
                self._carry = item
                # 여기까지 드롭된 것은 이 프레임 **앞**의 오디오다. 새 세션의 base 가 흡수한다.
                # 이 뒤로 드롭되는 것(새 세션이 열리기를 기다리는 동안 큐가 넘침)은 남긴다
                self._pending_silence_ms = 0
                self._pending_break = False
                await session.close_stream()
                return _ROTATE

            await self._send_frame(session, item, gap_ms, carried=carried)

    async def _send_frame(
        self, session: SttSession, item: Accepted, gap_ms: int, *, carried: bool = False
    ) -> None:
        if self._base_offset_ms is None:
            # 세션의 첫 프레임. 앞의 갭·드롭은 base 가 흡수하므로 무음을 보내지 않는다.
            # 단 세션을 갈며 넘겨받은 프레임이면 드롭은 그 **뒤**에서 일어났다 — 지우면 그만큼
            # 이후 전사 시각이 앞당겨진다. 남겨 두면 다음 프레임 앞에 무음으로 들어간다
            self._base_offset_ms = item.frame.offset_ms
            if not carried:
                self._pending_silence_ms = 0
                self._pending_break = False
        else:
            self._pending_silence_ms = 0
            if gap_ms:
                filled_from = self._bytes_sent // BYTES_PER_MS
                self._filled_ms.append((filled_from, filled_from + gap_ms))
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
            if not event.is_final:
                await self._send(message)
            else:
                # 받은 final 은 정리(취소)가 와도 끝까지 처리한다. 중간에 끊기면 화면에도 DB 에도
                # 남지 않는다. 화면 전송은 상대가 멈춰 있을 수 있어 상한을 둔다
                with anyio.CancelScope(shield=True):
                    if self._client is None:
                        # FE 가 떨어져 있다. interim 은 다음 것이 덮으니 버리고 final 만 모은다
                        self._missed_finals.append(message)
                        del self._missed_finals[:-MAX_MISSED_FINALS]
                    else:
                        with anyio.move_on_after(ABORT_TIMEOUT_SEC):
                            await self._send(message)
                    # 화면에 먼저 보내고 저장한다. 저장은 클라이언트가 붙어 있든 없든 한다 —
                    # 리포트의 원천은 이 행이지 화면이 아니다
                    await self._persist(message)
        if event.is_final:
            # 빈 final 도 구간을 닫는다. 다음 interim 은 새 번호로
            self.segment_no += 1
            # 빈 final 도 센다 — 말 없이 들은 구간이라는 뜻이다
            self._observe_silence(event)

    def _observe_silence(self, event: Transcript) -> None:
        try:
            silent = self._record.silence.observe(
                event.start_ms,
                event.end_ms,
                ((w.start_ms, w.end_ms) for w in event.words),
                has_text=bool(event.transcript.strip()),
                unheard=self._filled_ms,
            )
            # final 은 시각 순으로 온다. 이 final 앞에서 끝난 메운 구간은 다시 볼 일이 없다
            self._filled_ms = [(s, e) for s, e in self._filled_ms if e > event.end_ms]
        except Exception:
            # 자동 종료는 부가 기능이다. 여기서 예외가 새면 세션이 _LOST 로 처리돼 재접속한다
            logger.exception("말 없음 판단 실패 take=%s", self.take_id)
            return
        if silent:
            self._auto_end(AutoEndReason.SILENCE)

    async def _persist(self, message: TranscriptMessage) -> None:
        self._unsaved.append(self._segment(message))
        if len(self._unsaved) > MAX_UNSAVED_FINALS:
            dropped = self._unsaved.pop(0)
            logger.error(
                "저장 못 한 final 이 너무 많아 오래된 것을 버린다 take=%s seq=%d",
                self.take_id,
                dropped.seq,
            )
        await self._save_unsaved()

    def _segment(self, message: TranscriptMessage) -> TranscriptSegmentCreateDTO:
        return TranscriptSegmentCreateDTO(
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

    async def _save_unsaved(self) -> None:
        """저장 못 한 final 을 번호 순서대로 저장한다. 실패하면 거기서 멈추고 다음에 다시 한다.

        DB 가 죽어 있으면 하나씩 다 시도해도 다 실패한다. 첫 실패에서 멈춰야 final 하나마다
        DB 왕복이 하나로 끝난다.
        """
        while self._unsaved:
            segment = self._unsaved[0]
            try:
                await self._store.append(self.take_id, segment)
            except NonExistentTake:
                # Take 가 지워졌다. 지운 요청이 이 스트림을 끊기 직전에 온 final 이거나, 그 요청이
                # 이 스트림을 보지 못했다. 저장할 곳이 없으니 더 돌 이유가 없다
                logger.warning(
                    "Take 가 지워져 final 을 저장하지 못했다. 스트림을 끊는다 take=%s seq=%d",
                    self.take_id,
                    segment.seq,
                )
                self._unsaved.clear()
                self.discard()
                return
            except DuplicateTranscriptSegment:
                # 실패로 알았던 저장이 실제로는 커밋됐다 (커밋 뒤 연결이 끊김). 남겨 두면 뒤의
                # final 이 이것에 막혀 영영 저장되지 않는다
                logger.warning(
                    "이미 저장된 final 이다. 넘어간다 take=%s seq=%d", self.take_id, segment.seq
                )
            except Exception:
                # DB 장애로 스트림까지 멈추면 안 된다 — 화면 전사는 이미 나갔고 발표는 계속된다.
                # 전사 원문은 로그에 남기지 않는다 (seq 와 길이만)
                logger.exception(
                    "final 저장 실패 take=%s seq=%d len=%d 남은 개수=%d",
                    self.take_id,
                    segment.seq,
                    len(segment.transcript),
                    len(self._unsaved),
                )
                return
            self._unsaved.pop(0)

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
        _ended[self.take_id] = reason
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
        _spawn(self._finish_auto_end(reason), name=f"take-auto-end:{self.take_id}")

    async def _finish_auto_end(self, reason: AutoEndReason) -> None:
        """사용자가 stop 을 보냈을 때(`RealtimeSession._stop`)와 같은 순서로 정리한다.

        FE 가 그 사이 stop 을 보내도 괜찮다 — 양쪽 다 같은 스트림이 닫히기를 기다리고, 두 번
        보내는 closed 와 close 는 FE 가 이미 닫은 뒤라 조용히 버려진다.
        """
        await self._send(
            AutoEndMessage(reason=reason, message=self.auto_end_policy.message(reason))
        )
        if not await self.wait_closed(DRAIN_TIMEOUT_SEC + ABORT_TIMEOUT_SEC):
            logger.warning("자동 종료 drain 타임아웃 take=%s. 스트림을 끊는다", self.take_id)
            self.cancel()
            await self.wait_cancelled(ABORT_TIMEOUT_SEC)

        # 마지막 확정 전사를 저장한 뒤에 끝난 것으로 적는다 (`RealtimeSession._stop` 과 같은 이유)
        try:
            marked = await asyncio.wait_for(
                self._store.mark_ended(self.take_id, reason.value), MARK_AUTO_END_TIMEOUT_SEC
            )
        except Exception:
            # 못 적어도 정리는 계속한다. 이 프로세스 안에서는 _ended 가 재연결을 막는다
            logger.exception("자동 종료 이유 저장 실패 take=%s reason=%s", self.take_id, reason)
        else:
            if not marked:
                # 그 사이 Take 가 지워졌거나 이미 끝난 상태였다 (FE 의 stop 이 먼저 적은 것 포함)
                logger.warning(
                    "자동 종료를 적지 않았다 (Take 가 없거나 이미 끝남) take=%s reason=%s",
                    self.take_id,
                    reason,
                )

        client = self._client
        if client is None:
            # FE 가 떨어져 있다. 다시 붙으면 TAKE_ENDED 로 이유를 받는다
            return
        await self.send_status()
        await _quiet(client.close(code=1000))

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


# 돌아가는 백그라운드 태스크. asyncio 는 태스크를 약하게만 잡고 있어서 참조가 없으면 끝나기
# 전에 GC 될 수 있다 (asyncio.create_task 문서의 경고). 끝나면 스스로 빠진다
_background: set[asyncio.Task[None]] = set()


def _spawn(coro: Coroutine[object, object, None], *, name: str) -> None:
    task = asyncio.create_task(coro, name=name)
    _background.add(task)
    task.add_done_callback(_on_background_done)


def _on_background_done(task: asyncio.Task[None]) -> None:
    """아무도 await 하지 않는 태스크라 예외를 여기서 꺼내 남긴다. 안 그러면 GC 될 때
    "Task exception was never retrieved" 로만 찍히고 어느 Take 인지 알 수 없다."""
    _background.discard(task)
    if task.cancelled():
        return
    if (error := task.exception()) is not None:
        logger.error("백그라운드 태스크 실패 %s", task.get_name(), exc_info=error)


# ── 레지스트리 ────────────────────────────────────────────────────────

_streams: dict[uuid.UUID, TakeStream] = {}

# attach() 의 "확인 -> 생성" 이 원자적이어야 한다. 아니면 grace 만료 직후처럼 같은
# take_id 로 재연결이 동시에 두 개 오는 순간 **둘 다** `_streams.get()` 에서 "없다" 를
# 보고 각자 TakeStream·Deepgram 세션을 만든다. 그러면 하나는 `_streams` 덮어쓰기로
# 레지스트리에서 밀려나 고아가 되고, Deepgram 연결은 둘 생긴다.
#
# 락은 **take_id 별**이다. 프로세스 전체 락 하나면 is_stopping 핸드오버(최대 DRAIN_TIMEOUT_SEC
# + ABORT_TIMEOUT_SEC) 나 커서 조회(DB 왕복) 동안 다른 Take 의 연결까지 같이 멈춘다 —
# 동시 사용자가 늘면 그 지연이 체감된다 (코드 리뷰 지적). 다른 Take 끼리는 공유하는
# 상태가 없으니 서로 기다릴 이유가 없다. 락 객체는 쓰는 동안만 살아 있고 마지막으로
# 나가는 쪽이 치운다 — Take 마다 락이 영영 남지 않게.


@dataclass
class _KeyedLock:
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    # 락을 잡았거나 기다리는 코루틴 수. asyncio 는 단일 스레드라 await 사이에서만 바뀐다
    holders: int = 0


_attach_locks: dict[uuid.UUID, _KeyedLock] = {}


class _BoundedDict[K, V](OrderedDict[K, V]):
    """`MAX_REMEMBERED_TAKES` 개까지만 둔다. 넣거나 고친 것이 가장 새것이고, 오래된 것부터 버린다.

    값은 `d[k] = v` 로만 넣는다 — `setdefault` · `update` 는 C 구현이라 여기를 거치지 않는다.
    """

    def __setitem__(self, key: K, value: V) -> None:
        super().__setitem__(key, value)
        self.move_to_end(key)
        while len(self) > MAX_REMEMBERED_TAKES:
            self.popitem(last=False)


# 끝낸 Take. 값은 자동 종료 이유고, 사용자가 끝냈으면 None 이다. DB(`takes.status` · `end_reason`)가
# 원본이고 이건 그게 적히기 전의 틈을 막는다 — 마지막 확정 전사를 저장하고 DB 에 적기 전에 FE 가
# 다시 붙으면 새 스트림이 열린다. 프로세스가 다시 뜨면 비지만 그때는 DB 가 막는다
_ended: _BoundedDict[uuid.UUID, AutoEndReason | None] = _BoundedDict()


@dataclass
class _TakeRecord:
    """스트림이 바뀌어도 Take 동안 이어지는 자동 종료 상태. 프로세스가 다시 뜨면 비지만 그때는
    프레임의 Take 시각이 최대 시간을 막는다 (말 없음은 처음부터 다시 센다)."""

    silence: SilenceTracker
    # 앞서 닫힌 스트림들이 열려 있던 시간(초)의 합. 최대 시간의 서버 시계다
    streamed_sec: float = 0.0


_takes: _BoundedDict[uuid.UUID, _TakeRecord] = _BoundedDict()


def _take_record(take_id: uuid.UUID, policy: AutoEndPolicy) -> _TakeRecord:
    record = _takes.get(take_id)
    if record is None:
        record = _TakeRecord(silence=SilenceTracker(policy))
    # 다시 넣어 가장 새것으로 둔다. 진행 중인 Take 가 오래된 순으로 밀려나지 않게
    _takes[take_id] = record
    return record


@contextlib.asynccontextmanager
async def _attach_lock(take_id: uuid.UUID):
    entry = _attach_locks.setdefault(take_id, _KeyedLock())
    entry.holders += 1
    try:
        async with entry.lock:
            yield
    finally:
        entry.holders -= 1
        if entry.holders == 0 and _attach_locks.get(take_id) is entry:
            del _attach_locks[take_id]


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
    이 프로세스에서 끝낸 Take 면 `TakeEnded`.
    """
    async with _attach_lock(take_id):
        # 정리 중인 스트림을 기다리기 전에 본다. 끝낸 Take 라면 기다렸다 새로 열 이유가 없다.
        # 정리 중인 스트림은 새로 끝나지 않으므로(끝낼 때 정리보다 먼저 적는다) 한 번이면 된다
        if take_id in _ended:
            raise TakeEnded(_ended[take_id])

        stream = _streams.get(take_id)

        if stream is not None and stream.is_stopping:
            # 정리 중인 스트림에 붙이면 곧 사라질 객체에 오디오를 밀어 넣게 된다.
            # 끝나기를 기다렸다가 새로 만든다. 정상 종료(stop)와 같은 시간을 기다린다 —
            # 더 일찍 끊으면 Deepgram 이 아직 보내지 않은 마지막 문장이 사라진다
            if not await stream.wait_closed():
                # 그래도 안 끝나면 강제로 끊고, 끊긴 스트림이 실제로 끝나기를 기다린다.
                # forget() 만 하면 옛 스트림은 Deepgram 연결을 붙든 채 계속 돈다. 끝나기 전에
                # 아래에서 번호를 읽으면 옛 스트림이 저장 중인 seq 를 새 스트림이 또 쓴다
                stream.cancel()
                await stream.wait_cancelled(ABORT_TIMEOUT_SEC)
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


async def end_without_stop(take_id: uuid.UUID) -> bool:
    """WS `stop` 없이 Take 를 끝낸다 (FE 종료 흐름의 Take PUT). 지금 DB 에 적어도 되면 True.

    마이크를 못 열었거나 재연결을 포기한 FE 는 소켓을 정리해 `stop` 을 보내지 못한다. 그래도
    발표는 끝까지 가므로 REST 로 끝낼 때 여기를 거친다. 전사 순서를 지키려고 셋으로 나눈다.

    - `stop` 으로 끝내는 중이거나 연결이 붙어 있으면 False. 그 연결이 마지막 전사를 저장한 뒤 적는다
    - 연결 없이 남은 스트림(끊긴 뒤 30초)은 `stop` 처럼 남은 전사까지 저장하고 정리한 뒤 True
    - 스트림이 없으면 바로 True

    True 면 이 뒤로 같은 Take 로 붙는 연결은 `TakeEnded` 로 거절한다.
    """
    # attach() 와 같은 락이다. 정리하는 동안 붙으려는 연결은 기다렸다가 TakeEnded 를 받는다
    async with _attach_lock(take_id):
        stream = _streams.get(take_id)
        if stream is not None and (take_id in _ended or stream.has_client):
            return False
        _ended[take_id] = None
        if stream is None:
            return True
        stream.end()
        if not await stream.wait_closed():
            logger.warning("Deepgram drain 타임아웃 take=%s. 스트림을 끊는다", take_id)
            stream.cancel()
            await stream.wait_cancelled(ABORT_TIMEOUT_SEC)
        return True


# 떠난 Take 를 끝낼 타이머. 스트림이 새로 열리면 끈다 (`TakeStream.start`)
_abandon_timers: dict[uuid.UUID, asyncio.TimerHandle] = {}


def arm_abandon(
    take_id: uuid.UUID,
    store: TranscriptStore,
    *,
    closed_at: datetime,
    delay_sec: float | None = None,
) -> None:
    """`delay_sec`(기본 `ABANDON_AFTER_SEC`) 뒤에 Take 를 떠난 것으로 끝낸다. 걸려 있으면 다시 건다.

    `closed_at` 은 마지막으로 연결이 끊긴 시각이다. Take 의 끝난 시각으로 적는다 — 기다린 시간까지
    연습한 시간에 넣지 않는다.
    """
    _cancel_abandon(take_id)
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # 이벤트 루프 밖이다 (앱이 내려간 뒤의 정리). 다시 뜰 때 DB 를 보고 다시 건다
        return
    delay = ABANDON_AFTER_SEC if delay_sec is None else delay_sec
    _abandon_timers[take_id] = loop.call_later(
        max(0.0, delay), _on_abandon, take_id, store, closed_at
    )


def _cancel_abandon(take_id: uuid.UUID) -> None:
    timer = _abandon_timers.pop(take_id, None)
    if timer is not None:
        timer.cancel()


def _on_abandon(take_id: uuid.UUID, store: TranscriptStore, closed_at: datetime) -> None:
    _abandon_timers.pop(take_id, None)
    # DB 를 기다려야 해서 태스크로 돈다. 타이머 콜백은 await 할 수 없다
    _spawn(_end_abandoned(take_id, store, closed_at), name=f"take-abandon:{take_id}")


async def _end_abandoned(take_id: uuid.UUID, store: TranscriptStore, closed_at: datetime) -> None:
    # attach() 와 같은 락이다. 지금 다시 붙는 연결이 있으면 그쪽이 먼저 스트림을 열고, 끝내는 동안
    # 붙으려는 연결은 기다렸다가 TakeEnded 를 받는다
    async with _attach_lock(take_id):
        stream = _streams.get(take_id)
        if take_id in _ended or (stream is not None and not stream.is_finished):
            # 그 사이 끝났거나 (사용자 stop · 자동 종료 · Take PUT) 누가 다시 붙었다
            return
        try:
            marked = await asyncio.wait_for(
                store.mark_abandoned(take_id, closed_at), MARK_AUTO_END_TIMEOUT_SEC
            )
        except Exception:
            # 못 적으면 다음에 앱이 뜰 때 DB 를 보고 다시 건다
            logger.exception("떠난 Take 를 끝내지 못했다 take=%s", take_id)
            return
        if not marked:
            # 그 사이 Take 가 지워졌거나 이미 끝나 있었다
            logger.info("떠난 Take 를 적지 않았다 (Take 가 없거나 이미 끝남) take=%s", take_id)
            return
        _ended[take_id] = None
        logger.info("떠난 Take 를 끝냈다 take=%s closed_at=%s", take_id, closed_at.isoformat())


async def arm_abandoned_takes(store: TranscriptStore) -> None:
    """앱이 뜰 때 부른다. 진행 중으로 남은 Take 마다 떠난 것으로 끝낼 타이머를 다시 건다.

    앞 프로세스의 타이머와 끊긴 시각은 사라졌다. 그래서 마지막으로 활동한 시각(전사 저장 등)에서
    `ABANDON_AFTER_SEC` 뒤로 건다 — 이미 지났으면 바로 끝낸다. 그 안에 FE 가 다시 붙으면 꺼진다.
    """
    try:
        takes = await asyncio.wait_for(store.running_takes(), ARM_ABANDONED_TIMEOUT_SEC)
    except Exception:
        # 앱은 뜬다 (상한을 넘겨도). 이 Take 들은 다음에 연결이 붙었다 끊길 때 다시 걸린다
        logger.exception("진행 중인 Take 를 읽지 못해 떠난 Take 타이머를 걸지 못했다")
        return
    now = datetime.now(UTC)
    for take_id, last_active_at in takes:
        idle_sec = (now - last_active_at).total_seconds()
        arm_abandon(
            take_id, store, closed_at=last_active_at, delay_sec=ABANDON_AFTER_SEC - idle_sec
        )
    if takes:
        logger.info("진행 중으로 남은 Take %d개에 떠난 Take 타이머를 걸었다", len(takes))


async def discard(take_ids: Iterable[uuid.UUID]) -> None:
    """지운 Take 들의 스트림을 끊는다(`TakeStream.discard`). 스트림이 없는 Take 는 건너뛴다.

    Take 를 지우는 요청이 커밋한 뒤에 부른다. 먼저 끊으면 커밋이 실패했을 때 지워지지도 않은
    연습의 STT 만 끊긴다.
    """
    for take_id in take_ids:
        # 떠난 뒤 지운 Take 다. 끝낼 것이 없다
        _cancel_abandon(take_id)
        # attach() 가 이 Take 의 스트림을 만드는 중이면 끝나기를 기다린다. 안 그러면 방금 만든
        # 스트림을 못 보고 지나간다
        async with _attach_lock(take_id):
            stream = _streams.get(take_id)
        if stream is not None:
            stream.discard()


def active_count() -> int:
    return len(_streams)


async def stop_all() -> None:
    """앱 종료. 남은 스트림을 정리한다.

    정상 종료(CloseStream) 를 잠깐 기다렸다가 안 끝나면 끊는다. 끊은 뒤에도 정리
    (Deepgram 소켓 닫기) 가 끝나기를 기다린다 — 여기서 바로 돌아가면 이벤트 루프가
    닫히면서 "Task was destroyed but it is pending" 로 정리가 중간에 잘린다.
    """
    streams = list(_streams.values())
    _streams.clear()
    for stream in streams:
        stream.request_stop()
    for stream in streams:
        if not await stream.wait_closed(timeout=2.0):
            stream.cancel()
            await stream.wait_closed(timeout=ABORT_TIMEOUT_SEC)
    if _background:
        # 자동 종료 정리(이유를 DB 에 적기)가 아직 돌고 있을 수 있다. 여기서 잘리면 다시 뜬 뒤
        # 그 Take 에 다시 붙을 수 있다. 스트림은 위에서 다 닫혔으므로 정리에 남은 것은 DB 쓰기
        # (상한 MARK_AUTO_END_TIMEOUT_SEC)와 FE 에 closed 를 보내고 닫기뿐이다
        _, pending = await asyncio.wait(
            set(_background), timeout=MARK_AUTO_END_TIMEOUT_SEC + ABORT_TIMEOUT_SEC
        )
        for task in pending:
            # 루프가 닫히며 "pending 인 채로 사라졌다" 로만 남지 않게, 무엇이 남았는지 적고 끊는다
            logger.error("종료 때까지 끝나지 않은 백그라운드 태스크를 끊는다 %s", task.get_name())
            task.cancel()
        if pending:
            await asyncio.wait(pending, timeout=ABORT_TIMEOUT_SEC)
