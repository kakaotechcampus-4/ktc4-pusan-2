"""IP 별 호출 횟수 제한 (`core/rate_limit.py`). 시계를 바꿔 끼워 시간 창을 넘긴다."""

import logging

import pytest

from pitch_coach_backend.core.rate_limit import REJECT_LOG_SEC, RateLimiter


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


def _limiter(clock: FakeClock, *, limit: int = 3, window_sec: float = 60.0) -> RateLimiter:
    return RateLimiter("테스트", limit=limit, window_sec=window_sec, clock=clock)


def test_allows_up_to_the_limit_then_rejects(clock: FakeClock) -> None:
    limiter = _limiter(clock)

    assert [limiter.allow("1.1.1.1") for _ in range(4)] == [True, True, True, False]


def test_other_ips_are_counted_separately(clock: FakeClock) -> None:
    limiter = _limiter(clock, limit=1)

    assert limiter.allow("1.1.1.1")
    assert not limiter.allow("1.1.1.1")
    assert limiter.allow("2.2.2.2")


def test_window_starts_from_the_first_request_and_resets_after_it(clock: FakeClock) -> None:
    limiter = _limiter(clock, limit=1)
    assert limiter.allow("1.1.1.1")

    clock.now += 59.9
    assert not limiter.allow("1.1.1.1")

    clock.now += 0.1
    assert limiter.allow("1.1.1.1")


def test_rejected_requests_do_not_extend_the_window(clock: FakeClock) -> None:
    """거절한 요청은 세지 않는다. 계속 두드려도 창이 끝나면 다시 받는다."""
    limiter = _limiter(clock, limit=1)
    assert limiter.allow("1.1.1.1")

    for _ in range(10):
        clock.now += 5
        assert not limiter.allow("1.1.1.1")

    clock.now += 10
    assert limiter.allow("1.1.1.1")


def test_ips_whose_window_ended_are_dropped(clock: FakeClock) -> None:
    """한 번 오고 다시 오지 않는 IP 가 메모리에 쌓이지 않는다."""
    limiter = _limiter(clock)
    for n in range(100):
        limiter.allow(f"10.0.0.{n}")

    clock.now += 60
    limiter.allow("1.1.1.1")

    assert set(limiter._windows) == {"1.1.1.1"}


def test_rejections_are_logged_once_per_interval(
    clock: FakeClock, caplog: pytest.LogCaptureFixture
) -> None:
    limiter = _limiter(clock, limit=1, window_sec=REJECT_LOG_SEC * 10)
    limiter.allow("1.1.1.1")

    with caplog.at_level(logging.WARNING, logger="pitch_coach_backend.core.rate_limit"):
        for _ in range(5):
            limiter.allow("1.1.1.1")
        clock.now += REJECT_LOG_SEC
        limiter.allow("1.1.1.1")

    assert len(caplog.records) == 2
    assert "ip=1.1.1.1" in caplog.records[0].getMessage()


def test_reset_forgets_every_ip(clock: FakeClock) -> None:
    limiter = _limiter(clock, limit=1)
    limiter.allow("1.1.1.1")

    limiter.reset()

    assert limiter.allow("1.1.1.1")
