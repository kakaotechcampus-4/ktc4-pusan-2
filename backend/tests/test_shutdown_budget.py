"""앱을 내리는 데 걸리는 시간과 컨테이너가 기다려 주는 시간."""

import re
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]


def _be_service(compose: str) -> str:
    match = re.search(r"^  be:\n(.*?)(?=^  \S|\Z)", compose, re.MULTILINE | re.DOTALL)
    assert match, "docker-compose.yml 에 be 서비스가 없다"
    return match[1]


def test_container_waits_until_records_are_saved() -> None:
    """컨테이너는 SIGTERM 뒤 `stop_grace_period` 가 지나면 프로세스를 강제로 끝낸다. 그 전에 uvicorn
    이 열린 요청을 기다리는 시간과 lifespan 정리(스트림 닫기 · 기록 저장 · 백그라운드 정리)가 끝나야
    메모리의 기록을 잃지 않는다. 정리 상한을 늘리면 compose 도 같이 늘린다."""
    from pitch_coach_backend.main import shutdown_max_sec

    dockerfile = (BACKEND / "Dockerfile").read_text()
    graceful = re.search(r'"--timeout-graceful-shutdown",\s*"(\d+)"', dockerfile)
    assert graceful, "uvicorn 이 열린 요청을 끝없이 기다린다"
    compose = (BACKEND.parent / "infra" / "docker-compose.yml").read_text()
    grace = re.search(r"^    stop_grace_period:\s*(\d+)s$", _be_service(compose), re.MULTILINE)
    assert grace, "be 의 stop_grace_period 가 없으면 기본값 10초만 기다린다"

    assert int(graceful[1]) + shutdown_max_sec() < int(grace[1])


def test_close_waits_keep_their_relation() -> None:
    """부르는 쪽마다 다른 정리 상한의 관계 (`TakeStream.close_or_cancel`). 한쪽 값만 고치면 마지막
    문장이 끊기거나 새 Take 가 끝내는 중인 스트림을 못 기다린다."""
    from pitch_coach_backend.realtime.connection import session
    from pitch_coach_backend.realtime.stream import registry, runtime
    from pitch_coach_backend.realtime.stt import loop as stt_loop

    # stop 이 Deepgram 의 마지막 결과를 기다리기 전에 끊지 않는다
    assert session.STOP_TIMEOUT_SEC > stt_loop.DRAIN_TIMEOUT_SEC
    # 끝내는 중인 스트림을 기다리는 시간은 가장 긴 쪽인 자동 종료(알림 + 스스로 닫힘 + 취소 정리)다
    longest = (
        runtime.CONTROL_SEND_TIMEOUT_SEC
        + stt_loop.self_close_max_sec()
        + stt_loop.ABORT_TIMEOUT_SEC
    )
    assert longest == registry.ENDING_WAIT_SEC
    assert session.STOP_TIMEOUT_SEC + session.CANCEL_TIMEOUT_SEC <= registry.ENDING_WAIT_SEC
    # 앱을 내릴 때는 Deepgram 을 기다리지 않고 기록 저장에 시간을 남긴다
    assert registry.SHUTDOWN_CLOSE_WAIT_SEC < stt_loop.DRAIN_TIMEOUT_SEC
