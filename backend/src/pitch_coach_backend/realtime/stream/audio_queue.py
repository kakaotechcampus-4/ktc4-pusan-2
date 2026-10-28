"""Deepgram 으로 보낼 오디오 큐. 스트림(`runtime.TakeStream`)이 채우고 `stt/loop.py` 가 꺼낸다.

Deepgram 이 죽어 있는 동안에도 받은 오디오를 들고 있다가 다시 붙으면 보낸다. 차면 오래된 것부터
버리고, 버린 것은 콜백(`on_drop`)으로 돌려줘 유실로 센다.
"""

import asyncio
from collections.abc import Callable

from pitch_coach_backend.realtime.event_ingestion import Accepted


class AudioQueue:
    def __init__(self, maxsize: int, on_drop: Callable[[Accepted], None]) -> None:
        # None 은 끝 신호다. 앞에 든 오디오를 다 보낸 뒤 CloseStream 으로 정리한다
        self.queue: asyncio.Queue[Accepted | None] = asyncio.Queue(maxsize=maxsize)
        self._on_drop = on_drop

    def put(self, item: Accepted | None) -> None:
        """큐가 차면 **오래된 것부터** 버린다. 최신 오디오가 실시간 코칭에 쓸모 있다."""
        while True:
            try:
                self.queue.put_nowait(item)
                return
            except asyncio.QueueFull:
                dropped = self.queue.get_nowait()
                if dropped is None:
                    # stop 신호를 버리면 영영 안 끝난다. 되돌리고 새 항목을 버린다
                    self.queue.put_nowait(None)
                    if item is not None:
                        self._on_drop(item)
                    return
                self._on_drop(dropped)

    def drain(self) -> None:
        """남은 오디오를 비운다. Deepgram 에 닿지 못한 채 남은 것은 유실로 돌려준다."""
        while True:
            try:
                item = self.queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            if item is not None:
                self._on_drop(item)
