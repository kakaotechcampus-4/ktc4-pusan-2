"""Take 동안 스트림이 바뀌어도 이어지는 기록과, 스트림이 Take 별 상태에 알리는 자리.

registry 가 Take 마다 기록(`TakeRecord`)을 만들어 스트림(`runtime.TakeStream`)에 넘기고, 스트림은
알릴 일을 `StreamHost` 로 넘긴다. 그래서 runtime 은 registry 를 import 하지 않는다.
"""

import asyncio
import time
import uuid
from collections.abc import Coroutine
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

from pitch_coach_backend.coach.client import CoachClient
from pitch_coach_backend.coach.session import CoachSession, FinalizeSnapshot
from pitch_coach_backend.coach.store import CoachStore
from pitch_coach_backend.realtime.dto import AutoEndReason
from pitch_coach_backend.realtime.event_ingestion import AudioBudget
from pitch_coach_backend.realtime.storage.metrics import MetricBuffer, SlideBuffer
from pitch_coach_backend.realtime.storage.store import TranscriptStore
from pitch_coach_backend.realtime.storage.transcripts import FinalBuffer
from pitch_coach_backend.realtime.stream.auto_end import SilenceTracker

if TYPE_CHECKING:
    from pitch_coach_backend.realtime.stream.runtime import TakeStream


@dataclass(frozen=True)
class CoachDeps:
    """코치 AI 와 그 저장소. Take 를 끝낸 뒤 Take 결과를 만들 때도 쓴다."""

    client: CoachClient
    store: CoachStore


@dataclass
class TakeRecord:
    """스트림이 바뀌어도 Take 동안 이어지는 상태. 프로세스가 다시 뜨면 비지만 그때는 프레임의 Take
    시각이 최대 시간을 막는다 (말 없음은 처음부터 다시 센다).

    Take 마다 하나를 `state.takes` 에 두고 registry 가 스트림을 만들 때 넘긴다. 스트림은 받은 기록을
    갱신할 뿐 맵 · 잠금에는 손대지 않는다.
    """

    silence: SilenceTracker
    # 아직 저장하지 않은 1초 원자료 · 장 변경. 스트림이 바뀌어도 앞 스트림이 못 넣은 행이 이어진다
    metrics: MetricBuffer
    slides: SlideBuffer
    # 아직 저장하지 않은 확정 전사. 스트림이 바뀌어도 앞 스트림이 못 넣은 final 이 이어진다
    finals: FinalBuffer
    # 발표 중 코치. coach_state · 창 · 확정 시각 · 문장 끝 신호가 스트림이 바뀌어도 이어진다
    coach: CoachSession
    store: TranscriptStore
    # 앞서 닫힌 스트림들이 열려 있던 시간(초)의 합. 최대 시간의 서버 시계다
    streamed_sec: float = 0.0
    # Take 를 끝낼 때 finalize 재료를 만드는 작업. 끝내는 쪽이 여럿이어도(자동 종료와 그 뒤 온 stop)
    # 하나를 같이 기다리고 결과를 각자 들고 있는다. 코치의 기억을 놓을 때 같이 놓는다
    seal: asyncio.Task[FinalizeSnapshot | None] | None = None
    # grace 만료 전 스트림을 교체해도 10초 몫을 새로 주지 않는다. 끊겨 있던 시간도 서버
    # 시간으로 채우되 버킷 상한까지만 채운다.
    audio_budget: AudioBudget = field(default_factory=lambda: AudioBudget(time.monotonic() * 1000))


class StreamHost(Protocol):
    """스트림이 Take 별 상태(registry)에 알리는 자리. runtime 은 registry 를 import 하지 않는다.

    끝낸 Take 기록 · 스트림 맵 · 백그라운드 태스크는 `state.py`, 떠난 Take 타이머는 `abandon.py` 가
    들고 있다. registry 가 이 자리를 구현하고 스트림은 여기로만 알린다.
    """

    def stream_started(self, take_id: uuid.UUID) -> None:
        """스트림이 열렸다 (다시 붙었다). 떠난 것으로 보고 끝내려던 타이머를 끈다."""
        ...

    def stream_closed(
        self, stream: TakeStream, store: TranscriptStore, *, discarded: bool, tab_closed: bool
    ) -> None:
        """스트림이 닫혔다. 레지스트리에서 빼고, Take 가 끝나지 않았으면 떠난 Take 타이머를 건다."""
        ...

    def take_ended(self, take_id: uuid.UUID, reason: AutoEndReason | None) -> None:
        """이 프로세스에서 Take 를 끝냈다. DB 에 적히기 전이라도 같은 Take 로 다시 붙지 못하게 한다.

        `reason` 은 BE 가 자동으로 끝낸 이유다. 사용자가 끝냈으면 None 이고, 이미 적힌 이유를 지우지
        않는다.
        """
        ...

    def spawn(self, coro: Coroutine[object, object, None], *, name: str) -> None:
        """백그라운드로 돌린다. 앱을 내릴 때 registry 가 끝나기를 기다린다."""
        ...

    async def finish_take(
        self,
        take_id: uuid.UUID,
        store: TranscriptStore,
        *,
        end_reason: str | None,
        coach: CoachDeps | None,
    ) -> bool:
        """스트림을 닫은 뒤 Take 를 끝낸 것으로 적고 Take 결과를 띄운다 (`finish.finish_take`)."""
        ...
