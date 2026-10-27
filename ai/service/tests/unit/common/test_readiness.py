"""common/readiness.py: 시작할 때 불러오는 모델의 준비 상태와 로그."""

import logging
import threading

from pitch_coach_ai.common.readiness import Readiness


def test_nothing_to_load_is_ready_at_once() -> None:
    r = Readiness([])
    r.start()
    assert r.ready


def test_ready_only_after_every_loader_ran(log_records: list[logging.LogRecord]) -> None:
    release = threading.Event()
    seen: list[str] = []

    def first() -> None:
        release.wait(5)
        seen.append("first")

    def second() -> None:
        seen.append("second")

    r = Readiness([first, second])
    r.start()
    assert not r.ready
    release.set()
    assert r.wait(5)
    assert seen == ["first", "second"]
    lines = [rec.getMessage() for rec in log_records if rec.name == "pitch_coach_ai.readiness"]
    assert [line.split(" duration_ms=")[0] for line in lines] == [
        "warmup_done name=test_ready_only_after_every_loader_ran.<locals>.first",
        "warmup_done name=test_ready_only_after_every_loader_ran.<locals>.second",
    ]


def test_failed_loader_leaves_it_not_ready_and_logs_without_message(
    log_records: list[logging.LogRecord],
) -> None:
    def broken() -> None:
        raise RuntimeError("모델 파일 경로에 비밀이 섞인 메시지")

    r = Readiness([broken])
    r.start()
    assert not r.wait(1)
    (line,) = [rec.getMessage() for rec in log_records if rec.name == "pitch_coach_ai.readiness"]
    assert line.startswith("warmup_failed name=")
    assert "RuntimeError at" in line
    assert "비밀" not in line


def test_failed_loader_calls_on_failure_once() -> None:
    called: list[str] = []

    def broken() -> None:
        raise RuntimeError("x")

    r = Readiness(
        [broken, lambda: called.append("never")], on_failure=lambda: called.append("stop")
    )
    r.start()
    assert not r.wait(1)
    assert called == ["stop"]
