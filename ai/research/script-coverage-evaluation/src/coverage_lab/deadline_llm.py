"""요청 마감 · 호출 시간 제한 · 재시도를 서비스와 같은 규칙으로 거는 LLM 감싸개 (시간 측정용).

서비스(`ai/service/src/pitch_coach_ai/common/llm.py` 의 `StructuredLlm`)는 요청마다 마감 시각 하나를 모든
호출이 같이 쓰고, 호출 한 번은 별도 스레드에서 `timeout` 초까지만 기다리며, 실패하면 마감 안에서 1번만 다시 부른다.
연구 코어는 그 감싸개를 import 하지 않으므로 같은 규칙을 얇게 다시 만들어, 장 수별로 120초 안에 끝나는지 잰다.

- 호출 시작 전: 마감까지 남은 시간이 `timeout` 보다 짧으면 새 호출을 시작하지 않고 `TimeoutError`
  (처음 호출이면 마감을 이미 넘긴 것이고, 재시도면 직전 실패를 올리지 않고 시간 초과로 본다)
- 시간 초과: `TimeoutError` (코어가 `LLM_TIMEOUT` 으로 바꾼다). 버린 호출은 스레드에서 끝까지 돌 수 있다 (비용은 든다)
- 재시도: 시간 초과 · 연결 실패 · 429 · 5xx 같은 실패만 `max_retries` 번. 출력 형식 오류와 4xx 는 다시 불러도 같아 다시 부르지 않는다
- 기록(`CallLog`): 호출 수(재시도 포함) · 재시도 수 · 시간 초과 수 · 호출 한 번이 걸린 시간. 요청마다 `CallLog` 하나를 모든 LLM 이 같이 쓴다
"""

import statistics
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from typing import Any

# 호출을 돌리는 스레드. 시간 초과로 버린 호출도 끝날 때까지 스레드 하나를 쓰므로 넉넉히 둔다
_executor = ThreadPoolExecutor(max_workers=64, thread_name_prefix="deadline-llm")


class CallLog:
    """한 요청의 LLM 호출 기록. 장 여러 개를 동시에 처리하므로 잠금으로 보호한다."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.durations: list[float] = []  # 호출 한 번마다 (성공 · 실패 · 시간 초과 모두)
        self.retries = 0
        self.timeouts = 0

    def record(self, seconds: float, *, retry: bool, timed_out: bool) -> None:
        with self._lock:
            self.durations.append(seconds)
            self.retries += retry
            self.timeouts += timed_out

    @property
    def calls(self) -> int:
        return len(self.durations)

    def summary(self) -> dict:
        durations = self.durations
        return {
            "calls": len(durations),
            "retries": self.retries,
            "timeouts": self.timeouts,
            "call_median_seconds": round(statistics.median(durations), 2) if durations else None,
            "call_max_seconds": round(max(durations), 2) if durations else None,
        }


def _retryable(exc: BaseException) -> bool:
    """다시 불러 볼 만한 실패인가. 출력 형식 오류(다시 불러도 같음)와 429 를 뺀 4xx 는 아니다."""
    if type(exc).__name__ in ("ValidationError", "OutputParserException"):
        return False
    status = getattr(exc, "status_code", None)
    return not (isinstance(status, int) and status < 500 and status != 429)


class DeadlineLlm:
    """`.invoke(messages)` 만 있는 LLM 을 요청 마감 · 호출 시간 제한 · 재시도로 감싼다."""

    def __init__(
        self,
        inner: Any,
        *,
        deadline: float,
        log: CallLog,
        timeout: float = 12.0,
        max_retries: int = 1,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._inner, self._deadline, self._log = inner, deadline, log
        self._timeout, self._max_retries, self._clock = timeout, max_retries, clock

    def invoke(self, messages):
        last_error: BaseException | None = None
        for attempt in range(self._max_retries + 1):
            if self._deadline - self._clock() < self._timeout:
                # 마감 안에 끝낼 수 없는 호출은 시작하지 않는다
                raise TimeoutError("요청 마감까지 남은 시간이 호출 시간 제한보다 짧다")
            started = self._clock()
            future = _executor.submit(self._inner.invoke, messages)
            wait = min(self._timeout, max(0.0, self._deadline - self._clock()))
            timed_out = False
            try:
                result = future.result(timeout=wait)
            except FutureTimeout:
                future.cancel()
                timed_out, last_error = True, TimeoutError(f"{wait:g}초 안에 응답이 없다")
            except Exception as exc:  # noqa: BLE001  # 재시도할 실패인지 보고 그대로 올린다
                if not _retryable(exc):
                    self._log.record(self._clock() - started, retry=attempt > 0, timed_out=False)
                    raise
                last_error = exc
            else:
                self._log.record(self._clock() - started, retry=attempt > 0, timed_out=False)
                return result
            self._log.record(self._clock() - started, retry=attempt > 0, timed_out=timed_out)
        assert last_error is not None
        raise last_error
