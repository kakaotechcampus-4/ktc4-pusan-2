"""IP 별 호출 횟수 제한. 프로세스 메모리에서 센다.

시간 창은 IP 마다 처음 받은 때부터 잰다. 창 안에서 `limit` 번을 넘으면 창이 끝날 때까지 거절하고,
거절한 요청은 세지 않는다. 창이 끝나면 처음부터 다시 센다.

uvicorn 워커 하나 · 프로세스 하나가 전제다(README "의존 방향"). 워커를 늘리면 워커마다 따로 세서
실제 허용 횟수가 워커 수만큼 늘어난다.
"""

import logging
import threading
import time
from collections.abc import Callable

logger = logging.getLogger(__name__)

# 상한에 걸린 요청을 로그에 남기는 간격(초). 걸리는 동안은 요청마다 남기지 않는다
REJECT_LOG_SEC = 60.0


class RateLimiter:
    """IP 별로 시간 창 안의 횟수를 센다. 동기 엔드포인트가 스레드 풀에서 함께 부르므로 잠근다."""

    def __init__(
        self,
        name: str,
        *,
        limit: int,
        window_sec: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.name = name
        self.limit = limit
        self.window_sec = window_sec
        self._clock = clock
        self._lock = threading.Lock()
        # IP -> (창을 연 시각, 창 안에서 받은 횟수)
        self._windows: dict[str, tuple[float, int]] = {}
        self._swept_at = clock()
        self._logged_at: float | None = None

    def allow(self, ip: str) -> bool:
        with self._lock:
            now = self._clock()
            self._sweep(now)
            opened, count = self._windows.get(ip, (now, 0))
            if now - opened >= self.window_sec:
                opened, count = now, 0
            if count >= self.limit:
                if self._logged_at is None or now - self._logged_at >= REJECT_LOG_SEC:
                    self._logged_at = now
                    logger.warning("%s 요청이 상한이라 받지 않는다 ip=%s", self.name, ip)
                return False
            self._windows[ip] = (opened, count + 1)
            return True

    def reset(self) -> None:
        with self._lock:
            self._windows.clear()
            self._logged_at = None

    def _sweep(self, now: float) -> None:
        """창이 끝난 IP 를 지운다. 창 길이마다 한 번 돈다.

        그래서 메모리에 남는 IP 는 최근 창 길이의 두 배 안에 처음 온 것뿐이다.
        """
        if now - self._swept_at < self.window_sec:
            return
        self._swept_at = now
        self._windows = {
            ip: window for ip, window in self._windows.items() if now - window[0] < self.window_sec
        }
