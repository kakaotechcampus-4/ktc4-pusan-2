"""DeadlineLlm: 호출 시간 제한 · 재시도 · 요청 마감 규칙 (가짜 시계 · 가짜 LLM, 실제 호출 없음)."""

import threading

import pytest

from coverage_lab.deadline_llm import CallLog, DeadlineLlm

TIMEOUT = 0.05  # 호출 시간 제한 (시계 단위 = 초, 기다림은 실제 시간이라 짧게 둔다)


class FakeClock:
    def __init__(self, now: float = 0.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


class ScriptedLLM:
    """호출마다 정해 둔 동작을 한다: "ok" 는 바로 답, "hang" 은 풀어 줄 때까지 안 답함, 예외는 던짐."""

    def __init__(self, *steps) -> None:
        self.steps = list(steps)
        self.calls = 0
        self.release = threading.Event()

    def invoke(self, messages):
        step = self.steps[self.calls]
        self.calls += 1
        if step == "hang":
            self.release.wait(5)
            return "late"
        if isinstance(step, Exception):
            raise step
        return step


def _llm(inner, *, deadline=100.0, clock=None, log=None):
    log = log if log is not None else CallLog()
    wrapped = DeadlineLlm(
        inner, deadline=deadline, log=log, timeout=TIMEOUT, clock=clock or FakeClock()
    )
    return wrapped, log


class _HttpError(Exception):
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code


def test_success_is_recorded():
    inner = ScriptedLLM("answer")
    llm, log = _llm(inner)
    assert llm.invoke([]) == "answer"
    assert (log.calls, log.retries, log.timeouts) == (1, 0, 0)
    assert log.summary()["calls"] == 1


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


def test_server_error_is_retried_but_client_error_is_not():
    inner = ScriptedLLM(_HttpError(503), "answer")
    llm, log = _llm(inner)
    assert llm.invoke([]) == "answer"
    assert (log.calls, log.retries) == (2, 1)

    inner = ScriptedLLM(_HttpError(400), "answer")
    llm, log = _llm(inner)
    with pytest.raises(_HttpError):
        llm.invoke([])
    assert inner.calls == 1
    assert log.calls == 1


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
