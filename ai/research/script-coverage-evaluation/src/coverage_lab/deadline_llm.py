"""요청 마감 · 호출 시간 제한 · 재시도를 서비스와 같은 규칙으로 거는 LLM 감싸개 (시간 측정용).

서비스(`ai/service/src/pitch_coach_ai/common/llm.py` 의 `StructuredLlm`)는 요청마다 마감 시각 하나를 모든
호출이 같이 쓰고, 호출 한 번은 별도 스레드에서 `timeout` 초까지만 기다리며, 실패하면 마감 안에서 1번만 다시 부른다.
연구 코어는 그 감싸개를 import 하지 않으므로 같은 규칙을 얇게 다시 만들어, 장 수별로 120초 안에 끝나는지 잰다.

- 호출 시작 전: 마감까지 남은 시간이 `timeout` 보다 짧으면 새 호출을 시작하지 않는다. 직전 실패가 있으면 그 예외를,
  없으면 `TimeoutError` 를 올린다 (서비스와 같다)
- 시간 초과: 기다림이 끝나거나 SDK · 전송 계층이 시간 초과를 내면 `TimeoutError` (코어가 `LLM_TIMEOUT` 으로 바꾼다)
- 재시도: 서비스 `_classify` 와 같은 분류로, 시간 초과 · 연결 실패 · 429 · 5xx 만 `max_retries` 번.
  출력 형식 오류 · 거부 · 4xx · 그 밖의 예외는 다시 부르지 않는다
- 버린 호출: 시간 초과로 버린 호출도 스레드에서 끝까지 돈다. `wait_idle` 로 다음 측정 전에 끝나기를 기다려,
  앞 측정의 호출이 뒤 측정의 시간에 섞이지 않게 한다 (클라이언트의 전송 시간 제한이 버린 호출을 끝낸다)
- 기록(`CallLog`): 호출 수(재시도 포함) · 재시도 수 · 시간 초과 수 · 호출 한 번이 걸린 시간. 요청마다 `CallLog` 하나를 모든 LLM 이 같이 쓴다
"""

import statistics
import threading
import time
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor, wait
from concurrent.futures import TimeoutError as FutureTimeout
from typing import Any

import httpx
import openai

# 호출을 돌리는 스레드. 시간 초과로 버린 호출도 끝날 때까지 스레드 하나를 쓰므로 넉넉히 둔다
_executor = ThreadPoolExecutor(max_workers=64, thread_name_prefix="deadline-llm")
_pending: set[Future] = set()
_pending_lock = threading.Lock()


def _submit(fn, *args) -> Future:
    future = _executor.submit(fn, *args)
    with _pending_lock:
        _pending.add(future)
    future.add_done_callback(_forget)
    return future


def _forget(future: Future) -> None:
    with _pending_lock:
        _pending.discard(future)


def wait_idle(timeout: float) -> int:
    """돌고 있는 호출(버린 호출 포함)이 끝나기를 `timeout` 초까지 기다린다. 그래도 남은 호출 수를 돌려준다."""
    with _pending_lock:
        running = list(_pending)
    if running:
        wait(running, timeout=timeout)
    with _pending_lock:
        return len(_pending)


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


def classify(exc: BaseException) -> tuple[BaseException, bool, bool]:
    """(올릴 예외, 다시 부를까, 시간 초과인가). 서비스 `common/llm.py` 의 `_classify` 와 같은 순서다."""
    if isinstance(exc, openai.APITimeoutError | httpx.TimeoutException):
        return TimeoutError("LLM 응답 시간이 초과되었다"), True, True
    if isinstance(exc, openai.APIConnectionError | httpx.TransportError):
        return exc, True, False
    if isinstance(exc, openai.RateLimitError):
        return exc, True, False
    if isinstance(exc, openai.APIStatusError):
        return exc, exc.status_code >= 500, False  # 400 · 401 · 403 · 404 등은 다시 불러도 같다
    return exc, False, False  # 출력 형식 오류 · 거부 · 그 밖: 다시 부르지 않는다


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
                raise last_error or TimeoutError(
                    "요청 마감까지 남은 시간이 호출 시간 제한보다 짧다"
                )
            started = self._clock()
            future = _submit(self._inner.invoke, messages)
            wait_seconds = min(self._timeout, max(0.0, self._deadline - self._clock()))
            try:
                result = future.result(timeout=wait_seconds)
            except FutureTimeout:
                future.cancel()
                error, retryable, timed_out = (
                    TimeoutError(f"{wait_seconds:g}초 안에 응답이 없다"),
                    True,
                    True,
                )
            except Exception as exc:  # noqa: BLE001  # 서비스와 같은 분류로 나눈다
                error, retryable, timed_out = classify(exc)
            else:
                self._log.record(self._clock() - started, retry=attempt > 0, timed_out=False)
                return result
            self._log.record(self._clock() - started, retry=attempt > 0, timed_out=timed_out)
            last_error = error
            if not retryable:
                raise error
        assert last_error is not None
        raise last_error
