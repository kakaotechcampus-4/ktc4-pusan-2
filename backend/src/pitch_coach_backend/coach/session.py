"""한 Take 의 발표 중 코칭. Take 에 붙어 있어(`take_stream._TakeRecord`) WebSocket 연결 · STT
스트림보다 오래 산다.

    FE ── metrics (1초마다) ─▶ CoachSession ── POST /coach/evaluate ─▶ AI
    FE ◀─ coach ────────────── CoachSession ◀─ action · feedback · indicators · events ·
                                    │          coach_state
                                    └─ events → coach_events (CoachStore)

판단은 FE 의 `metrics` 가 올 때 한 번 한다. BE 가 타이머를 따로 돌리지 않는 이유는 시간축이다 —
코치의 `t_ms` 는 오디오 · 전사 단어와 같은 FE Take 시계여야 하고, 그 시계는 FE 에만 있다.

지키는 규칙 (`docs/ai/architecture.md` 1-3 2번 · 2절, `docs/ai/features/coach-agent.md` 12-2)
  - **요청은 한 번에 하나.** 앞 응답을 받기 전에 온 `metrics` 는 창 · 원자료에만 넣고 그 1초의
    호출은 건너뛴다(쌓아 두었다 보내지 않는다). 두 요청이 같은 `coach_state` 로 나가면 뒤의 응답이
    앞의 기억을 지운다. 시간 제한(300ms)을 넘긴 응답은 클라이언트가 버린다.
  - **몰아 온 `metrics`(`buffered`)로는 부르지 않는다.** 창에만 넣고 다음 실시간 `metrics` 에서
    부른다 — 그 요청의 창에 몰아 온 값이 들어 있다.
  - **확정 시각.** 확정 단어의 `final_at_ms` 는 그 결과를 받기 직전의 마지막 `metrics.t_ms` 다.
    `metrics` 의 시각을 갱신하는 일과 그 시각의 요청 창을 만드는 일은 `on_metrics` 하나에서 await
    없이 한다 — 그 사이에 새 확정 결과가 끼지 않아, 실시간 요청에 실린 단어는 모두 `final_at_ms` 가
    그 요청 `t_ms` 보다 작다. replay 가 같은 1초에 같은 단어를 다시 넣는다.
  - **`coach_state` 와 이벤트는 받은 그대로.** 해석하지 않고 다음 요청 · finalize 에 다시 싣는다.
  - **실패해도 발표는 계속된다.** AI 가 답하지 않으면 그 1초를 건너뛰고, 이벤트 저장이 실패하면
    들고 있다가 다음에 다시 넣는다. 끝내 못 넣은 이벤트는 finalize 저장이 같이 넣는다.

Take 를 시작할 때 읽은 값(`TakeContext`)이 없으면(읽기 실패) 코칭은 끄고, 창 · 확정 시각 · 문장 끝은
그대로 모은다 — 끝난 뒤 finalize 가 쓴다.
"""

import asyncio
import bisect
import contextlib
import logging
import uuid
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from pitch_coach_backend.coach.client import CoachClient, EvaluateReply
from pitch_coach_backend.coach.requests import (
    GAZE_VOICE_WINDOW_MS,
    WORDS_WINDOW_MS,
    FinalWord,
    MetricSecond,
    SlideEvent,
    TakeConstants,
    TakeContext,
    evaluate_request,
    slide_at,
    slide_changes,
    window_inputs,
)
from pitch_coach_backend.coach.store import CoachStore
from pitch_coach_backend.core.row_buffer import RowBuffer
from pitch_coach_backend.module.take.dto import StoredEvent
from pitch_coach_backend.module.take.exception import NonExistentTake

logger = logging.getLogger(__name__)

# 창보다 조금 더 들고 있는다. 늦게 온 1초 기록 · 단어가 바로 버려지지 않게
_KEEP_SLACK_MS = 5_000
# Take 를 끝낼 때 진행 중인 판단과 저장 못 한 이벤트를 기다리는 상한. 클라이언트는 300ms 에 끊는다.
# 이벤트는 못 넣어도 finalize 저장이 같이 넣는다
SEAL_WAIT_SEC = 2.0
# 저장 못 한 이벤트를 들고 있을 개수. 넘치면 오래된 것부터 버린다 (finalize 가 받은 것만 센다)
MAX_UNSAVED_EVENTS = 2_000
# FE 에 코치 메시지를 보내는 상한. 보내기는 판단 하나(`_inflight`) 안에서 돌아, 소켓이 막혀 기다리는
# 동안에는 다음 판단을 모두 건너뛴다. 넘기면 그 메시지는 버린다
NOTIFY_TIMEOUT_SEC = 1.0

# WS `coach` 를 보내는 쪽 (take_stream). 붙어 있는 클라이언트에게만 보낸다
type Notify = Callable[[int, EvaluateReply], Awaitable[None]]


def being_cancelled() -> bool:
    """지금 태스크가 취소되는 중인가. 기다리던 다른 태스크가 끊겨 난 CancelledError 와 가른다."""
    current = asyncio.current_task()
    return current is not None and current.cancelling() > 0


async def _cancel_and_wait(task: asyncio.Task[None]) -> None:
    """task 를 끊고 끝나기를 기다린다. 기다리는 동안 이 작업이 취소되면 그 취소는 위로 올린다."""
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        if being_cancelled():
            raise


@dataclass(frozen=True)
class FinalizeSnapshot:
    """Take 를 끝낼 때 코치 세션이 넘기는 것. finalize 요청의 재료다 (coach-agent.md 5-2 finalize).

    `t_ms` 는 Take 가 끝난 시각(마지막 `metrics` 의 바깥 `t_ms`)이고, 종료에 들어갈 때 고정한다.
    `constants` · `coach_state` 가 없으면(코칭이 꺼져 있었음 · 잃음) finalize 는 replay 를 붙인다.
    """

    t_ms: int
    inputs: dict[str, Any]
    constants: TakeConstants | None
    coach_state: dict[str, Any] | None
    # 이 세션이 받은 이벤트 전부, 받은 순서대로 (이벤트, 그 응답의 meta)
    events: tuple[StoredEvent, ...] = ()


@dataclass
class _Windows:
    """최근 창에 넣을 원자료. 시선 · 음량은 1초 시작 시각 → 기록, 같은 1초는 먼저 온 것."""

    gaze: dict[float, dict[str, Any]] = field(default_factory=dict)
    voice: dict[float, dict[str, Any]] = field(default_factory=dict)
    words: list[FinalWord] = field(default_factory=list)


class CoachSession:
    def __init__(self, take_id: uuid.UUID) -> None:
        self.take_id = take_id
        # Take 를 시작할 때 읽은 값과 AI · 저장소. 없으면 코칭을 하지 않는다 (`enable`)
        self._constants: TakeConstants | None = None
        self._client: CoachClient | None = None
        self._store: CoachStore | None = None
        self.notify: Notify | None = None

        self.coach_state: dict[str, Any] | None = None
        # 받은 metrics 중 가장 늦은 바깥 t_ms. 확정 단어의 확정 시각이다
        self.clock_ms: int | None = None
        # DB 의 (take_id, t_ms) 와 같은 중복 키. null 센서와 STT 상태도 첫 메시지의 값을 유지한다.
        self._seen_metrics: set[int] = set()
        # 설정 읽기가 늦어진 동안 받은 첫 메시지. DB 기록을 먼저 복원한 뒤 새 입력을 합친다.
        self._pending_metrics: list[MetricSecond] = []
        # 마지막 metrics 의 STT 상태. finalize 마지막 창에 싣는다 (`closed` 는 싣지 않는다)
        self.last_stt_status = "connecting"
        self._windows = _Windows()
        # Take 동안의 문장 끝 신호(정렬). Take 를 끝낼 때 takes.utterance_ends 에 한 번에 쓴다
        self._utterance_ends: list[int] = []
        # 장 변경. 시각 → 장 번호, 같은 시각은 먼저 온 것
        self._slides: dict[int, int] = {}
        self._slide_changes: list[dict[str, int]] = []

        self._inflight: asyncio.Task[None] | None = None
        # 마지막으로 보낸 요청의 t_ms. 그보다 이르거나 같은 metrics 로는 부르지 않는다
        self._requested_ms: int | None = None
        self._stopped = False
        # Take 를 끝내며 원자료 버퍼를 닫았다 (`seal_inputs`). 이 뒤로 온 1초 · 장 변경은 안 받는다
        self._inputs_sealed = False
        # Take 가 끝나 기억을 놓았다 (`release`). 이 뒤로는 아무것도 반영하지 않는다
        self._released = False
        # COACHING 모드인가 (`enable`). 아니면 FE 에 말을 걸지 않는다
        self._speaks = False
        # 마지막으로 FE 에 보낸 상태 표시. 바뀔 때만 보낸다
        self._indicators: dict[str, str] = {}
        # 받은 이벤트 전부, 받은 순서대로. finalize 에 넘긴다
        self.events: list[StoredEvent] = []
        # 받는 대로 coach_events 에 넣는다. 실패하면 들고 있다가 다시 넣는다 (1초 원자료와 같다)
        self._event_buffer: RowBuffer[StoredEvent] | None = None
        # 앞 판단이 안 끝나 건너뛴 1초. AI 가 느려지면 는다
        self.skipped = 0

    @property
    def enabled(self) -> bool:
        return self._constants is not None

    @property
    def released(self) -> bool:
        return self._released

    def enable(self, context: TakeContext, *, client: CoachClient, store: CoachStore) -> None:
        """Take 를 시작할 때 읽은 값으로 코칭을 켠다. 이미 켜져 있으면 그대로 둔다 — 다시 붙은
        연결이 Take 를 시작할 때의 값과 `coach_state` 를 이어 간다. 놓은 세션은 켜지 않는다."""
        if self.enabled or self._released:
            return
        self._constants = context.constants
        self._client = client
        self._store = store
        self._speaks = context.constants.common()["mode"] == "COACHING"
        self._event_buffer = RowBuffer(
            self.take_id,
            store.append_events,
            what="코치 이벤트",
            batch=1,
            key=lambda stored: stored[0].get("t_ms", 0),
            max_unsaved=MAX_UNSAVED_EVENTS,
            gone_errors=(NonExistentTake,),
        )
        pending_slides, self._slides = self._slides, {}
        self._slide_changes = []
        for event in context.slides:
            self.on_slide(event)
        for started_ms, number in pending_slides.items():
            self.on_slide(SlideEvent(number, started_ms))
        # DB 에 저장된 메시지가 같은 바깥 t_ms 의 첫 기록이다. 설정을 못 읽던 동안 이미 받은
        # 재전송도 DB 값(null 포함)으로 되돌리고, 아직 DB 에 없는 새 입력은 받은 순서로 합친다.
        # 단어 · 문장 끝 · 장 변경은 유지한다. 복원만으로 AI 를 부르지 않는다.
        pending, self._pending_metrics = self._pending_metrics, []
        self._windows.gaze.clear()
        self._windows.voice.clear()
        self._seen_metrics.clear()
        self.clock_ms = None
        self.last_stt_status = "connecting"
        for second in (*context.seconds, *pending):
            self.on_metrics(
                second.t_ms,
                gaze=second.gaze,
                voice=second.voice,
                stt_status=second.stt_status or "connecting",
                buffered=True,
            )

    # ── 입력 ──────────────────────────────────────────────────────────

    def on_metrics(
        self,
        t_ms: int,
        *,
        gaze: Mapping[str, Any] | None,
        voice: Mapping[str, Any] | None,
        stt_status: str,
        buffered: bool,
    ) -> None:
        """FE 의 1초 측정값. 창에 넣고, 부를 때면 그 시각으로 한 번 판단한다.

        여기서 await 하지 않는다 — 시각 갱신과 창 만들기 사이에 확정 결과가 끼면 안 된다.
        """
        if self._released or self._inputs_sealed:
            # Take 가 끝나 놓았거나 원자료 버퍼를 닫았다. 저장되지 않는 1초를 창에 넣지 않는다
            return
        if t_ms in self._seen_metrics:
            return
        self._seen_metrics.add(t_ms)
        if not self.enabled:
            self._pending_metrics.append(
                MetricSecond(
                    t_ms,
                    None,
                    dict(gaze) if gaze is not None else None,
                    dict(voice) if voice is not None else None,
                    stt_status,
                )
            )
        # 같은 1초는 시작 시각 그대로 가린다 — replay 와 같은 규칙이어야 같은 창이 나온다
        if gaze is not None:
            self._windows.gaze.setdefault(gaze["t_ms"], dict(gaze))
        if voice is not None:
            self._windows.voice.setdefault(voice["t_ms"], dict(voice))
        if self.clock_ms is None or t_ms >= self.clock_ms:
            self.clock_ms = t_ms
            if stt_status != "closed":
                self.last_stt_status = stt_status
        self._prune()

        if buffered or self._stopped or not self.enabled:
            return
        if t_ms != self.clock_ms or (self._requested_ms is not None and t_ms <= self._requested_ms):
            # 이미 지난 시각이다 (같은 1초를 또 보냄 · 몰아 온 것보다 이른 실시간 metrics).
            # 그 창에는 확정 시각이 요청보다 늦은 단어가 섞여 replay 와 달라진다
            return
        if self._inflight is not None and not self._inflight.done():
            self.skipped += 1
            return
        assert self._constants is not None
        request = evaluate_request(
            self._constants, t_ms, self._inputs_at(t_ms, stt_status), self.coach_state
        )
        self._requested_ms = t_ms
        self._inflight = asyncio.create_task(
            self._evaluate(t_ms, request), name=f"coach-evaluate:{self.take_id}"
        )

    def on_final(self, words: Iterable[tuple[str, int, int]]) -> int:
        """STT 확정 단어 (단어, 시작, 끝). 그 구간의 확정 시각(`final_at_ms`)을 돌려준다."""
        final_at_ms = self.clock_ms or 0
        if self._released:
            return final_at_ms
        self._windows.words.extend(
            FinalWord(word, start_ms, end_ms, final_at_ms) for word, start_ms, end_ms in words
        )
        return final_at_ms

    def on_utterance_end(self, ms: int) -> None:
        """Deepgram 문장 끝 신호. 그 신호가 알린 마지막 단어의 끝 시각(Take 기준 ms)이다."""
        if self._released:
            return
        at = bisect.bisect_left(self._utterance_ends, ms)
        if at == len(self._utterance_ends) or self._utterance_ends[at] != ms:
            self._utterance_ends.insert(at, ms)

    def on_slide(self, event: SlideEvent) -> None:
        if self._released or self._inputs_sealed or event.started_ms in self._slides:
            return
        self._slides[event.started_ms] = event.slide_number
        self._slide_changes = slide_changes(
            (SlideEvent(number, started_ms) for started_ms, number in self._slides.items()),
            normalize_start=False,
        )

    @property
    def utterance_ends(self) -> list[int]:
        return list(self._utterance_ends)

    # ── 판단 ──────────────────────────────────────────────────────────

    def _inputs_at(self, t_ms: int, stt_status: str) -> dict[str, Any]:
        # 문장 끝 신호는 Take 내내 쌓인다. 창(60초) 언저리만 꺼내 넘긴다 — 고르는 건 window_inputs
        recent = self._utterance_ends[
            bisect.bisect_left(self._utterance_ends, t_ms - WORDS_WINDOW_MS) :
        ]
        return window_inputs(
            t_ms,
            gaze_records=self._windows.gaze.values(),
            voice_records=self._windows.voice.values(),
            words=self._windows.words,
            utterance_ends=recent,
            stt_status=stt_status,
            slide=slide_at(t_ms, self._slide_changes),
        )

    def _prune(self) -> None:
        if self.clock_ms is None:
            return
        record_lo = self.clock_ms - GAZE_VOICE_WINDOW_MS - _KEEP_SLACK_MS
        word_lo = self.clock_ms - WORDS_WINDOW_MS - _KEEP_SLACK_MS
        for records in (self._windows.gaze, self._windows.voice):
            for start in [s for s in records if s < record_lo]:
                del records[start]
        if self._windows.words and self._windows.words[0].end_ms < word_lo:
            self._windows.words = [w for w in self._windows.words if w.end_ms >= word_lo]

    async def _evaluate(self, t_ms: int, request: dict[str, Any]) -> None:
        assert self._client is not None and self._constants is not None
        try:
            reply = await self._client.evaluate(request)
        except Exception:
            # 클라이언트는 실패를 None 으로 돌려준다. 여기 오면 버그다 — 발표는 멈추지 않는다
            logger.exception("코치 판단 중 예상 못 한 오류 take=%s t_ms=%d", self.take_id, t_ms)
            return
        if reply is None or self._released:
            # 그 사이 Take 가 끝나 기억을 놓았다 (지움 · 떠남 · Take 결과를 띄움)
            return
        self.coach_state = reply.coach_state
        for event in reply.events:
            stored = (event, reply.meta)
            self.events.append(stored)
            if self._event_buffer is not None:
                self._event_buffer.add(stored)
        await self._maybe_notify(t_ms, reply)

    async def _maybe_notify(self, t_ms: int, reply: EvaluateReply) -> None:
        """`COACHING` 모드에서 말을 걸거나 상태 표시가 바뀌면 FE 에 보낸다 (1-3 2번)."""
        if self._stopped or not self._speaks:
            # 끝낸 뒤 도착한 응답은 상태 · 이벤트만 반영한다. 끝난 발표에 말을 걸지 않는다
            return
        # 빈 indicators 는 '바뀌지 않음'이다. 이전 표시를 그대로 둔다
        changed = bool(reply.indicators) and reply.indicators != self._indicators
        if reply.indicators:
            self._indicators = dict(reply.indicators)
        if not (reply.intervened or changed) or self.notify is None:
            return
        try:
            await asyncio.wait_for(self.notify(t_ms, reply), NOTIFY_TIMEOUT_SEC)
        except Exception:
            # 소켓이 막혔다. FE 가 지금 표시를 모를 수 있어 다음 표시를 바뀐 것으로 보고 다시 보낸다
            logger.warning("코치 메시지를 보내지 못했다 take=%s", self.take_id, exc_info=True)
            self.forget_display()

    def forget_display(self) -> None:
        """FE 가 아는 상태 표시를 잊는다. 새 연결은 표시를 모르므로 다음 표시를 바뀐 것으로 보고
        보낸다 — 떨어져 있는 동안 바뀐 표시가 다음 변화까지 안 보이지 않게."""
        self._indicators = {}

    # ── 종료 ──────────────────────────────────────────────────────────

    def stop(self) -> None:
        """Take 를 끝낸다. 이 뒤로는 판단하지 않는다 (1-3 3번 ①).

        창에는 `seal_inputs` 까지 계속 넣는다 — FE 가 stop 앞에 보내는 마지막 1초 조각과 STT 를 닫은
        뒤의 마지막 확정 단어가 finalize 마지막 창에 든다."""
        self._stopped = True

    def seal_inputs(self) -> None:
        """1초 원자료 · 장 변경 버퍼를 닫을 때 같이 부른다. 이 뒤로 온 `metrics` · `slide` 는 창에도
        넣지 않는다 — 저장되지 않은 1초가 마지막 창 · 끝난 시각에 들면 replay 와 달라진다."""
        self._inputs_sealed = True

    async def seal(self) -> FinalizeSnapshot | None:
        """Take 를 끝낼 때 부른다. 진행 중인 판단을 기다려 반영하고(늦으면 버린다), 저장 못 한
        이벤트를 한 번 더 넣어 본 뒤 finalize 재료를 돌려준다. `metrics` 가 하나도 없었으면 None.
        """
        self.stop()
        self.seal_inputs()
        if self._released:
            # 이미 놓았다 (떠난 Take 를 끝내기 PUT 이 되살림). finalize 가 DB 에서 다시 만든다
            return None
        inflight = self._inflight
        if inflight is not None and not inflight.done():
            try:
                await asyncio.wait_for(asyncio.shield(inflight), SEAL_WAIT_SEC)
            except TimeoutError:
                await _cancel_and_wait(inflight)
            except asyncio.CancelledError:
                # 판단이 다른 곳에서 끊겼다 (앱 종료). 이 작업이 취소된 게 아니면 재료를 마저 만든다
                if being_cancelled():
                    raise
        buffer = self._event_buffer
        if buffer is not None:
            # 못 넣은 것은 finalize 저장이 같이 넣는다 (재료의 events 에 다 있다)
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(buffer.flush(), SEAL_WAIT_SEC)
        if self._released or self.clock_ms is None:
            # 기다리는 사이 놓았다 (Take 를 지움). 만들 재료가 없다
            return None
        return FinalizeSnapshot(
            t_ms=self.clock_ms,
            inputs=self._inputs_at(self.clock_ms, self.last_stt_status),
            constants=self._constants,
            coach_state=self.coach_state,
            events=tuple(self.events),
        )

    def release(self) -> None:
        """Take 가 끝났다. 창 · 기억을 놓는다 (`coach_state` 는 한 Take 에 15KB 안팎)."""
        self._stopped = True
        self._released = True
        self._speaks = False
        if self._inflight is not None and not self._inflight.done():
            self._inflight.cancel()
        self._inflight = None
        if self._event_buffer is not None:
            # 이 뒤로는 넣지 않는다. 돌던 저장이 실패해도 다시 넣지 않는다 — finalize 저장이 같은
            # 이벤트를 넣는데, 둘이 같이 seq 를 매기면 부딪힌다
            self._event_buffer.close()
        self._event_buffer = None
        # 끝난 스트림 · 연결 · Take 를 시작할 때 읽은 값(계획 · 미션 · 대본)을 붙잡지 않는다
        self.notify = None
        self._constants = None
        self._client = None
        self._store = None
        self.coach_state = None
        self._windows = _Windows()
        self._seen_metrics.clear()
        self._pending_metrics.clear()
        self._utterance_ends = []
        self._slides = {}
        self._slide_changes = []
        self.events = []

    async def shutdown(self, timeout: float) -> None:
        """앱을 내린다. 진행 중인 판단을 끊고, 저장 못 한 이벤트를 한 번 더 넣어 본다."""
        self._stopped = True
        inflight = self._inflight
        if inflight is not None and not inflight.done():
            await _cancel_and_wait(inflight)
        buffer = self._event_buffer
        if buffer is not None:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(buffer.flush(), timeout)
            buffer.close()
