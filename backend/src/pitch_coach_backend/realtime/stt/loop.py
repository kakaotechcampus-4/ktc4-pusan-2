"""Deepgram 세션 루프. 스트림(`stream/runtime.py`)이 담은 오디오를 Deepgram 에 보내고 결과를 넘긴다.

    TakeStream ──큐──▶ SttLoop ──(WS-2)── Deepgram
                ◀──콜백── (전사 · 문장 끝 신호 · STT 상태)

- **WS-2 재접속** (BE↔Deepgram): 큐에 담아 두고 백오프로 재접속한다. 새 세션은 타임스탬프가 다시
  0 부터라 `base_offset_ms` 를 새로 잡고 `stt_session_no` 를 올린다. 재접속이 이어서 실패해도
  **연결을 끊지 않는다** — FE 1단 코치는 계속 돌아야 하므로 `degraded` 만 알리고 재시도한다.
- 타임라인 규칙: 한 Deepgram 세션 안에서는 `take_ms = base_offset_ms + deepgram_ms` 가 성립해야
  한다. 그래서 프레임 사이 갭은 무음으로 메운다. 메우기에 너무 긴 갭(`MAX_SILENCE_FILL_MS` 초과)은
  **세션을 갈아** base 를 다시 잡는다 — 일부만 메우면 그 뒤 전사 시각이 통째로 앞당겨진다.

Deepgram 세션마다 바뀌는 상태(base · 보낸 길이 · 메운 무음 · 넘겨받은 프레임)와 세션 번호는 여기
있다. FE 연결 · Take 상태 · 저장은 모른다 — 전사 · 문장 끝 신호 · 상태 변화는 스트림이 넘긴 콜백으로
돌려준다. connection · stream 을 import 하지 않는다.
"""

import asyncio
import contextlib
import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import replace

import anyio
from websockets.exceptions import ConnectionClosed

from pitch_coach_backend.realtime.audio import BYTES_PER_MS, silence
from pitch_coach_backend.realtime.dto import SttState
from pitch_coach_backend.realtime.event_ingestion import MAX_SILENCE_FILL_MS, Accepted
from pitch_coach_backend.realtime.stt.adapter import (
    Metadata,
    SttAdapter,
    SttConfig,
    SttConfigRejected,
    SttConnectError,
    SttError,
    SttSession,
    Transcript,
    UtteranceEnd,
)

logger = logging.getLogger(__name__)

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

# _run_session 이 끝난 이유
_STOPPED = "stopped"  # CloseStream 을 보냈다. Take 가 끝났다
_ROTATE = "rotate"  # 타임라인이 끊겨 새 세션이 필요하다
_LOST = "lost"  # Deepgram 이 끊었다


async def _quiet(awaitable: Awaitable[object]) -> None:
    """닫힌 소켓을 닫을 때 나는 예외는 무시한다 (`stream/runtime.py` 의 `_quiet` 과 같다)."""
    with contextlib.suppress(Exception):
        await awaitable


def _backoff(attempt: int) -> float:
    return BACKOFF_SEC[min(attempt, len(BACKOFF_SEC)) - 1]


class SttLoop:
    def __init__(
        self,
        take_id: uuid.UUID,
        *,
        adapter: SttAdapter,
        config: SttConfig,
        queue: asyncio.Queue[Accepted | None],
        stop_event: asyncio.Event,
        is_stopping: Callable[[], bool],
        on_state: Callable[[SttState], Awaitable[None]],
        on_transcript: Callable[[Transcript], Awaitable[None]],
        on_utterance_end: Callable[[int], None],
        stt_session_no: int = 0,
    ) -> None:
        self.take_id = take_id
        self._adapter = adapter
        self._config = config
        # 스트림이 담는 오디오 큐와 stop 신호. `None` 이 들어오면 CloseStream 으로 끝낸다
        self._queue = queue
        self._stop_event = stop_event
        self._is_stopping = is_stopping
        self._on_state = on_state
        self._on_transcript = on_transcript
        # 문장 끝 신호. 마지막 단어의 끝 시각을 Take 기준(ms)으로 바꿔 넘긴다
        self._on_utterance_end = on_utterance_end
        # Take 기준이다. 스트림이 새로 만들어질 때 저장된 마지막 값에서 이어 받는다
        self.stt_session_no = stt_session_no
        self._ever_connected = False

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

    @property
    def base_offset_ms(self) -> int | None:
        """지금 Deepgram 세션의 첫 프레임 Take 시각. 세션의 첫 프레임을 보내기 전에는 None."""
        return self._base_offset_ms

    @property
    def filled_ms(self) -> list[tuple[int, int]]:
        """이 세션에 BE 가 메워 보낸 무음 구간 (Deepgram 세션 시각 ms). 말 없음에서 뺀다."""
        return self._filled_ms

    def forget_filled(self, before_ms: int) -> None:
        """final 은 시각 순으로 온다. 그 final 앞에서 끝난 메운 구간은 다시 볼 일이 없다."""
        self._filled_ms = [(s, e) for s, e in self._filled_ms if e > before_ms]

    def on_dropped(self, lost_ms: int, *, timeline_break: bool) -> None:
        """스트림이 큐에서 오디오를 버렸다. 그만큼 다음 프레임 앞에 무음을 채운다."""
        self._pending_silence_ms += lost_ms
        if timeline_break:
            self._pending_break = True

    async def run(self) -> None:
        """Deepgram 세션을 필요한 만큼 이어서 연다.

        **루프 조건에 `not self._is_stopping()` 을 두지 않는다.** 회전(`_ROTATE`)과 stop 이
        겹치면(회전 직전에 넘겨받은 `self._carry` 오디오 한 프레임이 아직 안 나갔는데
        `request_stop()` 이 먼저 온 경우) 스트림의 `_stopping` 은 이미 True 다. 거기서 루프
        조건만으로 재진입을 막으면 `continue` 를 써도 다음 세션이 절대 안 열려서
        캐리 프레임과 그 뒤 큐에 쌓인 stop 신호(`None`) 를 통째로 못 보내고 끝난다.
        그래서 재진입 여부는 오직 `_run_session` 의 반환값(`reason`) 으로만 정한다.
        """
        attempt = 0
        while True:
            session = await self._connect()
            if session is None:
                attempt += 1
                if attempt >= FAST_ATTEMPTS:
                    # 재시도는 계속한다. 여기서 연결을 끊으면 FE 1단 코치까지 같이 죽는다
                    await self._on_state("degraded")
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
            await self._on_state("ok")

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
            if self._is_stopping():
                break
            await self._on_state("reconnecting")
            attempt = 1
            if await self._sleep_or_stop(_backoff(attempt)):
                break

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
                case UtteranceEnd():
                    # 문장 끝 신호. 그 마지막 단어의 끝 시각을 Take 기준으로 바꿔 코치에 넘긴다
                    if self._base_offset_ms is not None:
                        self._on_utterance_end(self._base_offset_ms + event.last_word_end_ms)
                case SttError():
                    logger.warning(
                        "Deepgram 오류 take=%s %s %s", self.take_id, event.code, event.message
                    )
                case _:
                    # SpeechStarted 는 쓰지 않는다
                    pass

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
