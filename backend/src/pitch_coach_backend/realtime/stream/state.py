"""Take 별 스트림 · 기록을 담는 프로세스 상태. 프로세스에 하나이고 요청 · 연결 · 재연결이 같이 쓴다.

registry · finish 만 쓴다. 밖(connection · live_takes)은 둘의 공개 함수를 부른다.
스트림(`runtime.TakeStream`)은 여기를 직접 만지지 않고 `record.StreamHost` 로 알린다.

uvicorn 워커 하나 · 프로세스 하나가 전제다. 워커를 늘리면 이 상태가 프로세스마다 생겨 같은 Take 의
연결이 다른 스트림에 붙는다. 그때는 스트림 소유권 · 요청 라우팅 · 공유 상태를 따로 설계한다.
"""

import asyncio
import contextlib
import uuid
from collections import OrderedDict
from collections.abc import Coroutine, Hashable
from dataclasses import dataclass, field

from pitch_coach_backend.coach.session import CoachSession
from pitch_coach_backend.core.tasks import TaskRegistry
from pitch_coach_backend.realtime.dto import AutoEndReason
from pitch_coach_backend.realtime.storage.metrics import MetricBuffer, SlideBuffer
from pitch_coach_backend.realtime.storage.store import TranscriptStore
from pitch_coach_backend.realtime.stream.abandon import LeftReason
from pitch_coach_backend.realtime.stream.auto_end import AutoEndPolicy, SilenceTracker
from pitch_coach_backend.realtime.stream.record import TakeRecord
from pitch_coach_backend.realtime.stream.runtime import TakeStream

# 끝낸 Take · Take 별 자동 종료 상태를 프로세스 안에서 기억하는 개수. 오래된 것부터 버린다
MAX_REMEMBERED_TAKES = 10_000


# 돌아가는 백그라운드 태스크 (자동 종료 · 떠난 Take 정리 · 늦은 종료 기록). 실패는 이름과 함께
# 로그에 남는다
background = TaskRegistry()


def spawn(coro: Coroutine[object, object, None], *, name: str, key: Hashable | None = None) -> None:
    background.spawn(coro, name=name, key=key)


# ── 레지스트리 ────────────────────────────────────────────────────────
streams: dict[uuid.UUID, TakeStream] = {}


# attach() 의 "확인 -> 생성" 이 원자적이어야 한다. 아니면 grace 만료 직후처럼 같은
# take_id 로 재연결이 동시에 두 개 오는 순간 **둘 다** `streams.get()` 에서 "없다" 를
# 보고 각자 TakeStream·Deepgram 세션을 만든다. 그러면 하나는 `streams` 덮어쓰기로
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


attach_locks: dict[uuid.UUID, _KeyedLock] = {}
# 스트림 자리 확보는 사용자별로 직렬화한다. 다른 사용자의 연결은 기다리지 않는다.
owner_locks: dict[uuid.UUID, _KeyedLock] = {}


@contextlib.asynccontextmanager
async def attach_lock(take_id: uuid.UUID):
    async with _keyed_lock(attach_locks, take_id):
        yield


@contextlib.asynccontextmanager
async def stream_owner_lock(owner_id: uuid.UUID):
    async with _keyed_lock(owner_locks, owner_id):
        yield


@contextlib.asynccontextmanager
async def _keyed_lock(locks: dict[uuid.UUID, _KeyedLock], key: uuid.UUID):
    entry = locks.setdefault(key, _KeyedLock())
    entry.holders += 1
    try:
        async with entry.lock:
            yield
    finally:
        entry.holders -= 1
        if entry.holders == 0 and locks.get(key) is entry:
            del locks[key]


class _BoundedDict[K, V](OrderedDict[K, V]):
    """`MAX_REMEMBERED_TAKES` 개까지만 둔다. 넣거나 고친 것이 가장 새것이고, 오래된 것부터 버린다.

    값은 `d[k] = v` 로만 넣는다 — `setdefault` · `update` 는 C 구현이라 여기를 거치지 않는다.
    """

    def __setitem__(self, key: K, value: V) -> None:
        super().__setitem__(key, value)
        self.move_to_end(key)
        while len(self) > MAX_REMEMBERED_TAKES:
            self.popitem(last=False)


# 끝낸 Take. 값은 BE 가 끝낸 이유(자동 종료 · 떠남)고, 사용자가 끝냈으면 None 이다.
# DB(`takes.status` · `end_reason`)가 원본이고 이건 그게 적히기 전의 틈을 막는다 — 마지막 확정
# 전사를 저장하고 DB 에 적기 전에 FE 가 다시 붙으면 새 스트림이 열린다. 프로세스가 다시 뜨면
# 비지만 그때는 DB 가 막는다
ended: _BoundedDict[uuid.UUID, AutoEndReason | LeftReason | None] = _BoundedDict()

takes: _BoundedDict[uuid.UUID, TakeRecord] = _BoundedDict()


def take_record(take_id: uuid.UUID, policy: AutoEndPolicy, store: TranscriptStore) -> TakeRecord:
    record = takes.get(take_id)
    if record is None:
        record = TakeRecord(
            silence=SilenceTracker(policy),
            metrics=MetricBuffer(take_id, store),
            slides=SlideBuffer(take_id, store),
            coach=CoachSession(take_id),
            store=store,
        )
    # 다시 넣어 가장 새것으로 둔다. 진행 중인 Take 가 오래된 순으로 밀려나지 않게
    takes[take_id] = record
    return record
