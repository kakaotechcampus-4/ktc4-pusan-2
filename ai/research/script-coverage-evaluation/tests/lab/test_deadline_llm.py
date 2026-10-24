"""DeadlineLlm: 호출 시간 제한 · 재시도 · 요청 마감 규칙 (가짜 시계 · 가짜 LLM, 실제 호출 없음)."""

import threading

import httpx
import openai
import pytest
from langchain_core.exceptions import OutputParserException
from pydantic import BaseModel, ValidationError

import coverage_lab.deadline_llm as deadline_llm
from coverage_lab.deadline_llm import CallLog, DeadlineLlm, wait_idle

TIMEOUT = 0.05  # 호출 시간 제한 (시계 단위 = 초, 기다림은 실제 시간이라 짧게 둔다)
_REQUEST = httpx.Request("POST", "http://llm.test/v1/chat/completions")


def _status(code: int) -> openai.APIStatusError:
    response = httpx.Response(code, request=_REQUEST)
    if code == 429:
        return openai.RateLimitError("rate limited", response=response, body=None)
    return openai.APIStatusError(f"status {code}", response=response, body=None)


def _validation_error() -> ValidationError:
    class Out(BaseModel):
        n: int

    with pytest.raises(ValidationError) as caught:
        Out.model_validate({"n": "숫자 아님"})
    return caught.value


class FakeClock:
    def __init__(self, now: float = 0.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


class ScriptedLLM:
    """호출마다 정해 둔 동작을 한다: "hang" 은 풀어 줄 때까지 안 답함, 예외는 던짐, 그 밖은 그 값을 답한다.

    `clock` · `advance` 를 주면 호출마다 가짜 시계를 그만큼 앞으로 민다 (걸린 시간 기록을 보려고).
    """

    def __init__(self, *steps, clock: FakeClock | None = None, advance: float = 0.0) -> None:
        self.steps = list(steps)
        self.calls = 0
        self.release = threading.Event()
        self.clock, self.advance = clock, advance

    def invoke(self, messages):
        step = self.steps[self.calls]
        self.calls += 1
        if self.clock is not None:
            self.clock.now += self.advance
        if step == "hang":
            self.release.wait(5)
            return "late"
        if isinstance(step, BaseException):
            raise step
        return step


def _llm(inner, *, deadline=100.0, clock=None, log=None):
    log = log if log is not None else CallLog()
    wrapped = DeadlineLlm(
        inner, deadline=deadline, log=log, timeout=TIMEOUT, clock=clock or FakeClock()
    )
    return wrapped, log


def test_success_is_recorded_with_its_duration():
    clock = FakeClock()
    inner = ScriptedLLM("answer", clock=clock, advance=1.5)
    llm, log = _llm(inner, clock=clock)
    assert llm.invoke([]) == "answer"
    assert (log.calls, log.retries, log.timeouts) == (1, 0, 0)
    assert log.durations == [1.5]
    assert log.summary()["call_median_seconds"] == 1.5


def test_failed_call_durations_are_recorded():
    clock = FakeClock()
    inner = ScriptedLLM(_status(503), "answer", clock=clock, advance=2.0)
    llm, log = _llm(inner, clock=clock)
    assert llm.invoke([]) == "answer"
    assert log.durations == [2.0, 2.0]
    assert log.summary()["call_max_seconds"] == 2.0


def test_wait_is_the_call_timeout_or_the_remaining_time(monkeypatch):
    """future.result 에 넘기는 기다림 = min(호출 시간 제한, 마감까지 남은 시간)."""
    waits = []

    class FakeFuture:
        def result(self, timeout):
            waits.append(timeout)
            return "answer"

        def cancel(self):
            return False

    monkeypatch.setattr(deadline_llm, "_submit", lambda fn, *args: FakeFuture())
    clock = FakeClock()
    llm, _ = _llm(ScriptedLLM(), deadline=100.0, clock=clock)
    llm.invoke([])
    clock.now = 100.0 - TIMEOUT * 1.5  # 남은 시간이 시간 제한보다 길지만 곧 마감
    llm.invoke([])
    assert waits == [TIMEOUT, TIMEOUT]
    wrapped = DeadlineLlm(ScriptedLLM(), deadline=100.0, log=CallLog(), timeout=10.0, clock=clock)
    clock.now = 85.0  # 남은 15초 > 시간 제한 10초
    wrapped.invoke([])
    assert waits[-1] == 10.0


def test_timeout_then_retry_succeeds():
    inner = ScriptedLLM("hang", "answer")
    llm, log = _llm(inner)
    try:
        assert llm.invoke([]) == "answer"
    finally:
        inner.release.set()
    assert (log.calls, log.retries, log.timeouts) == (2, 1, 1)


def test_timeout_twice_raises_timeout_error():
    inner = ScriptedLLM("hang", "hang")
    llm, log = _llm(inner)
    try:
        with pytest.raises(TimeoutError):
            llm.invoke([])
    finally:
        inner.release.set()
    assert (log.calls, log.retries, log.timeouts) == (2, 1, 2)


def test_sdk_timeout_is_a_timeout_and_is_retried():
    """SDK · 전송 계층의 시간 초과도 TimeoutError 로 바꾸고 시간 초과로 센다 (서비스 `_classify` 와 같다)."""
    inner = ScriptedLLM(openai.APITimeoutError(request=_REQUEST), httpx.ReadTimeout("느림"))
    llm, log = _llm(inner)
    with pytest.raises(TimeoutError):
        llm.invoke([])
    assert inner.calls == 2
    assert (log.calls, log.retries, log.timeouts) == (2, 1, 2)


def test_no_new_call_when_remaining_time_is_short():
    inner = ScriptedLLM("answer")
    llm, log = _llm(inner, deadline=TIMEOUT / 2)  # 시작부터 남은 시간이 시간 제한보다 짧다
    with pytest.raises(TimeoutError):
        llm.invoke([])
    assert inner.calls == 0
    assert log.calls == 0


def test_no_retry_when_deadline_runs_out_during_first_call():
    clock = FakeClock()

    class AdvancingHang(ScriptedLLM):
        def invoke(self, messages):
            clock.now = 99.99  # 마감(100)까지 시간 제한보다 짧게 남기고 응답하지 않는다
            return super().invoke(messages)

    inner = AdvancingHang("hang", "answer")
    llm, log = _llm(inner, clock=clock)
    try:
        with pytest.raises(TimeoutError):
            llm.invoke([])
    finally:
        inner.release.set()
    assert inner.calls == 1
    assert (log.calls, log.retries, log.timeouts) == (1, 0, 1)


def test_skipped_retry_keeps_the_previous_error():
    """재시도할 시간이 모자라면 직전 실패를 그대로 올린다 (시간 초과로 바꾸지 않는다, 서비스와 같다)."""
    clock = FakeClock()
    error = _status(503)
    inner = ScriptedLLM(error, "answer", clock=clock, advance=100.0 - TIMEOUT / 2)
    llm, log = _llm(inner, clock=clock)
    with pytest.raises(openai.APIStatusError) as raised:
        llm.invoke([])
    assert raised.value is error
    assert inner.calls == 1


@pytest.mark.parametrize(
    "error",
    [_status(503), _status(500), _status(429), openai.APIConnectionError(request=_REQUEST)],
    ids=["503", "500", "429", "connection"],
)
def test_retryable_failures_are_retried_once(error):
    inner = ScriptedLLM(error, "answer")
    llm, log = _llm(inner)
    assert llm.invoke([]) == "answer"
    assert (inner.calls, log.calls, log.retries, log.timeouts) == (2, 2, 1, 0)


@pytest.mark.parametrize(
    "error",
    [
        _status(400),
        _status(404),
        _validation_error(),
        OutputParserException("형식이 다름"),
        ValueError("그 밖의 예외"),
    ],
    ids=["400", "404", "validation", "output-parser", "other"],
)
def test_other_failures_are_not_retried(error):
    inner = ScriptedLLM(error, "answer")
    llm, log = _llm(inner)
    with pytest.raises(type(error)):
        llm.invoke([])
    assert (inner.calls, log.calls, log.retries) == (1, 1, 0)


def test_wait_idle_waits_for_abandoned_calls():
    """시간 초과로 버린 호출은 계속 돈다. wait_idle 이 그 호출이 끝나기를 기다린다."""
    inner = ScriptedLLM("hang", "hang")
    llm, _ = _llm(inner)
    with pytest.raises(TimeoutError):
        llm.invoke([])
    assert wait_idle(timeout=0.01) == 2  # 두 호출 모두 아직 돈다
    inner.release.set()
    assert wait_idle(timeout=5) == 0


def test_log_summary_has_median_and_max():
    log = CallLog()
    for seconds in (1.0, 3.0, 2.0):
        log.record(seconds, retry=False, timed_out=False)
    summary = log.summary()
    assert (summary["calls"], summary["call_median_seconds"], summary["call_max_seconds"]) == (
        3,
        2.0,
        3.0,
    )
