"""Take 자동 종료 조건. 네트워크도 Deepgram 도 모른다 (event_ingestion.py 와 같은 위치).

두 조건 중 하나라도 맞으면 TakeStream 이 Take 를 끝낸다.

1. **말 없음** — 최근 `silence_end_ms` 동안 말이 없고, 직전 `silence_window_ms` 중
   `silence_ratio` 이상이 말 없음이다.
2. **최대 시간** — Take 시간이 `max_duration_ms` 를 넘었다. FE 가 보내는 Take 시각(프레임
   `offset_ms`)이 기준이고, 서버 시계(`server_clock_slack_ms` 만큼 늦게)는 시각이 멈춘 Take 를
   막는 보조 상한이다.

말 없음은 FE 의 소리 크기가 아니라 **전사**로 판단한다. 선풍기·생활 소음은 소리는 크지만
말이 아니다. 그리고 시간은 벽시계가 아니라 **전사가 확인된 오디오** 로 잰다. Deepgram 은
말이 없는 구간에도 빈 final 을 보내 "여기까지 들었다" 를 알려 준다. 그 길이만 센다.
그래서 STT 가 죽어 있거나(`reconnecting`·`degraded`) 오디오가 유실된 구간은 말 없음으로
치지 않는다 — 그 구간을 세면 STT 장애가 곧 Take 강제 종료가 된다. BE 가 타임라인을 맞추려고
메워 보낸 무음(프레임 사이 틈 · 큐가 넘쳐 버린 오디오)도 같은 이유로 뺀다. Deepgram 은 그 구간에도
빈 final 을 주지만 들은 것이 아니다. 반대로 Deepgram 이 아무 결과도 주지 않으면 말 없음은 영영
안 걸린다. 그때는 최대 시간이 막는다.
"""

import asyncio
import time
from collections import deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from pitch_coach_backend.core.config import settings
from pitch_coach_backend.realtime.dto import AutoEndReason

# 서버 시계가 최대 시간보다 늦게 끝내는 여유. 서버 시계는 스트림이 열려 있던 시간이라 무대 시계보다
# 조금 빠르다 (Deepgram 연결 · grace). 정상 Take 는 프레임 시각이 먼저 끝내게 한다.
# AI 요청의 `t_ms` 상한(1시간 + 60초)과 같은 여유다
SERVER_CLOCK_SLACK_MS = 60_000


@dataclass(frozen=True)
class AutoEndPolicy:
    """자동 종료 조건. 값 검사는 여기 한 곳에서 한다 — 설정에서 만들 때(`from_settings`, 앱 시작
    때 한 번 부른다)도, 테스트가 직접 만들 때도."""

    silence_end_ms: int
    silence_window_ms: int
    silence_ratio: float
    max_duration_ms: int
    server_clock_slack_ms: int = SERVER_CLOCK_SLACK_MS

    def __post_init__(self) -> None:
        if min(self.silence_end_ms, self.silence_window_ms, self.max_duration_ms) <= 0:
            raise ValueError("자동 종료 시간은 0 보다 커야 합니다.")
        if not 0 < self.silence_ratio <= 1:
            raise ValueError("말 없음 비율은 0 초과 1 이하여야 합니다.")
        if self.silence_end_ms > self.silence_window_ms:
            # 창 안에서는 조건을 영영 확인할 수 없다
            raise ValueError("말 없음 구간은 창보다 길 수 없습니다.")
        if self.server_clock_slack_ms < 0:
            raise ValueError("서버 시계 여유는 0 이상이어야 합니다.")

    @classmethod
    def from_settings(cls) -> AutoEndPolicy:
        return cls(
            silence_end_ms=settings.take_silence_end_sec * 1000,
            silence_window_ms=settings.take_silence_window_sec * 1000,
            silence_ratio=settings.take_silence_ratio,
            max_duration_ms=settings.take_max_duration_sec * 1000,
        )

    def message(self, reason: AutoEndReason) -> str:
        """FE 가 그대로 보여 줄 문구."""
        match reason:
            case AutoEndReason.SILENCE:
                return f"{_duration(self.silence_end_ms)} 넘게 말이 없어 연습을 자동으로 끝냈어요."
            case AutoEndReason.TIME_LIMIT:
                return f"연습은 최대 {_duration(self.max_duration_ms)}까지라 자동으로 끝냈어요."


def _duration(ms: int) -> str:
    """나누어떨어지는 가장 큰 단위로. 3_600_000 → "1시간", 120_000 → "2분", 90_000 → "90초"."""
    if ms % 3_600_000 == 0:
        return f"{ms // 3_600_000}시간"
    if ms % 60_000 == 0:
        return f"{ms // 60_000}분"
    return f"{ms // 1000}초"


@dataclass
class _Chunk:
    duration_ms: int
    speech_ms: int


class SilenceTracker:
    """final 전사를 받아 말 없음 조건을 확인한다.

    시간 축은 "전사가 확인된 오디오" 다. final 하나가 덮는 구간에서 BE 가 메운 무음(`unheard`)을
    뺀 길이를 차례로 이어 붙인 축이라, 중간에 STT 가 죽었거나 오디오를 잃은 구간은 축에서 빠진다.
    빠진 구간은 말 없음 연속을 끊지도 늘리지도 않는다. 창은 이 축에서 최근 `silence_window_ms`
    만큼이다. 오래된 구간을 통째로 버리므로 창은 final 하나 길이(보통 몇 초)만큼 넘칠 수 있다 —
    분 단위 조건에서는 무시할 만하다.

    Take 하나에 하나다. 스트림이 새로 열려도(grace 만료 뒤 재연결) 이어 쓴다 — 스트림마다 새로
    만들면 자주 끊겼다 붙는 Take 는 말 없음이 끝내 안 걸린다.
    """

    def __init__(self, policy: AutoEndPolicy) -> None:
        self._policy = policy
        self._chunks: deque[_Chunk] = deque()
        self._observed_ms = 0
        self._speech_ms = 0
        # 마지막 말이 끝난 뒤로 확인된 말 없음
        self.silent_run_ms = 0

    def observe(
        self,
        start_ms: int,
        end_ms: int,
        words: Iterable[tuple[int, int]],
        *,
        has_text: bool,
        unheard: Iterable[tuple[int, int]] = (),
    ) -> bool:
        """final 하나를 더한다. 말 없음 조건을 만족하면 True.

        `words` 는 (start_ms, end_ms) 단어 구간, `unheard` 는 BE 가 메워 보낸 무음 구간이고
        둘 다 final 과 같은 시간 축이다.
        """
        if end_ms <= start_ms:
            # Deepgram 이 길이 없는 결과를 보냈다. 확인된 오디오가 없으니 세지 않는다
            return False
        holes = _merge(_clip(unheard, start_ms, end_ms))
        heard = end_ms - start_ms - _total(holes)
        if heard <= 0:
            # 이 final 이 덮는 구간이 전부 BE 가 메운 무음이다. 들은 오디오가 없다
            return False

        # final 구간 밖으로 삐져나온 단어는 잘라 낸다. 길이가 없는 단어는 버린다
        spans = _merge(_clip(words, start_ms, end_ms))
        if spans:
            speech = min(_total(spans), heard)
            last_end = spans[-1][1]
            # 마지막 말 뒤로 들은 시간만 센다
            self.silent_run_ms = end_ms - last_end - _total(_clip(holes, last_end, end_ms))
        elif has_text:
            # 글자는 있는데 단어 시각이 없거나 구간 밖이다. 말이 있었는지 확실하지 않으면
            # 말한 것으로 본다 — 잘못 끝내는 쪽이 늦게 끝내는 쪽보다 나쁘다
            speech = heard
            self.silent_run_ms = 0
        else:
            speech = 0
            self.silent_run_ms += heard

        self._chunks.append(_Chunk(heard, speech))
        self._observed_ms += heard
        self._speech_ms += speech
        while self._observed_ms - self._chunks[0].duration_ms >= self._policy.silence_window_ms:
            old = self._chunks.popleft()
            self._observed_ms -= old.duration_ms
            self._speech_ms -= old.speech_ms
        return self.should_end()

    def should_end(self) -> bool:
        if self.silent_run_ms < self._policy.silence_end_ms:
            return False
        # silent_run_ms > 0 이면 _observed_ms 도 0 보다 크다
        silent_ratio = (self._observed_ms - self._speech_ms) / self._observed_ms
        return silent_ratio >= self._policy.silence_ratio


class TimeLimit:
    """최대 시간. 기준은 프레임의 Take 시각(`reached`)이고, 서버 시계(`start`)는 그 시각이 가지 않는
    Take(오디오 없이 연결만 붙여 둠, offset 이 멈춤)를 막는 보조 상한이다. 스트림마다 하나다."""

    def __init__(self, policy: AutoEndPolicy, on_expire: Callable[[], None]) -> None:
        self._policy = policy
        self._on_expire = on_expire
        self._timer: asyncio.TimerHandle | None = None
        # 이 스트림을 연 시각(monotonic). 닫힐 때 열려 있던 시간을 Take 의 서버 시계에 더한다
        self._opened_at: float | None = None

    def start(self, streamed_sec: float) -> None:
        """서버 시계를 켠다. `streamed_sec` 은 앞서 닫힌 스트림들이 열려 있던 시간(초)의 합이다.

        - 스트림을 열 때 건다. 첫 프레임에서 걸면 오디오를 안 보내고 연결만 붙여 둔 Take 는
          영영 안 끝나고 Deepgram 연결도 계속 살아 있다.
        - 재는 것은 이 Take 의 스트림이 **열려 있던 시간의 합**이다. 스트림 하나 기준이면 grace
          (30초)보다 오래 끊었다 다시 붙을 때마다 1시간이 새로 시작하고, 벽시계 기준이면 탭이 죽어
          있던 시간까지 세어 이어서 연습하자마자 끝난다. FE 무대에는 일시정지가 없어서 열려 있던
          시간은 무대 시계와 거의 같다 — 조금 빠른 만큼(연결 · grace) 여유를 둔다.
        """
        self._opened_at = time.monotonic()
        self._timer = asyncio.get_running_loop().call_later(
            max(0.0, self._limit_sec() - streamed_sec), self._on_expire
        )

    def reached(self, take_ms: int) -> bool:
        """프레임의 Take 시각(offset)으로 최대 시간을 본다. 최대 시간의 기준이다 — FE 무대 시계라
        새로고침 · 재연결에도 이어지고, 프로세스가 다시 떠 서버 시계를 잃어도 여기서 걸린다.
        서버 시계(`start`)는 이 시각이 가지 않을 때만 대신 끝낸다."""
        return take_ms >= self._policy.max_duration_ms

    def stop(self) -> float:
        """서버 시계를 끄고 이 스트림이 열려 있던 초를 돌려준다. 켜지 않았거나 이미 껐으면 0."""
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
        if self._opened_at is None:
            return 0.0
        opened = time.monotonic() - self._opened_at
        self._opened_at = None
        return opened

    def _limit_sec(self) -> float:
        """서버 시계로 잰 최대 시간. 프레임 시각이 먼저 끝내도록 여유를 더한다."""
        return (self._policy.max_duration_ms + self._policy.server_clock_slack_ms) / 1000


def _clip(spans: Iterable[tuple[int, int]], lo: int, hi: int) -> list[tuple[int, int]]:
    """[lo, hi) 안으로 자른다. 길이가 없어진 구간은 버린다."""
    clipped = ((max(s, lo), min(e, hi)) for s, e in spans)
    return [(s, e) for s, e in clipped if e > s]


def _merge(spans: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """겹치거나 맞닿은 구간을 합쳐 시작 순으로. 단어가 겹쳐 와도 두 번 세지 않는다."""
    merged: list[tuple[int, int]] = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def _total(spans: list[tuple[int, int]]) -> int:
    return sum(e - s for s, e in spans)
