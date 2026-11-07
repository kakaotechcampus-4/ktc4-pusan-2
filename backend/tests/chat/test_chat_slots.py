"""대화 자리 설정과 시작 검사 (`core/ai_slots.py`, chat-agent.md L2-13).

자리 수가 2 미만이거나 프로세스 수 × 자리 수가 승인된 전체 상한을 넘으면 스위치가 켜져 있어도
대화 기능만 꺼진다. 다른 기능은 그대로 뜬다.
"""

import asyncio
import logging
import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient

from pitch_coach_backend.core import ai_slots
from pitch_coach_backend.core.config import settings
from pitch_coach_backend.module.chat.availability import chat_enabled
from tests.chat.api_helpers import bearer


def _slots(
    monkeypatch: pytest.MonkeyPatch, *, per_process: int, processes: int, total: int
) -> None:
    monkeypatch.setattr(settings, "chat_ai_slots_per_process", per_process)
    monkeypatch.setattr(settings, "web_concurrency", processes)
    monkeypatch.setattr(settings, "chat_ai_slots_total", total)


@pytest.mark.parametrize(
    ("per_process", "processes", "total", "enabled"),
    [
        (4, 1, 4, True),
        (2, 2, 4, True),
        (4, 2, 8, True),
        # 다시 평가가 두 자리를 한 번에 잡지 못한다
        (1, 1, 4, False),
        (4, 2, 4, False),
        (5, 1, 4, False),
        # 워커 수 0 도 프로세스 하나로 뜬다
        (5, 0, 4, False),
    ],
)
def test_chat_is_on_only_when_the_slot_settings_pass(
    monkeypatch: pytest.MonkeyPatch, per_process: int, processes: int, total: int, enabled: bool
) -> None:
    monkeypatch.setattr(settings, "agent_enabled", True)
    _slots(monkeypatch, per_process=per_process, processes=processes, total=total)

    assert chat_enabled() is enabled
    assert (ai_slots.chat_slots_problem() is None) is enabled


def test_switch_off_keeps_chat_off_with_valid_slots(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "agent_enabled", False)
    _slots(monkeypatch, per_process=4, processes=1, total=4)

    assert chat_enabled() is False


@pytest.mark.usefixtures("chat_app")
def test_bad_slot_settings_answer_like_the_switch_is_off(
    client: TestClient, user_id: uuid.UUID, monkeypatch: pytest.MonkeyPatch
) -> None:
    _slots(monkeypatch, per_process=4, processes=2, total=4)

    availability = client.get("/api/chat/availability", headers=bearer(user_id))
    assert availability.json() == {"enabled": False}
    listing = client.get("/api/chat/conversations", headers=bearer(user_id))
    assert (listing.status_code, listing.json()["code"]) == (503, "AGENT_DISABLED")
    # 대화 밖의 API 는 그대로다
    assert client.get("/api/health").json() == {"status": "ok"}


@pytest.mark.parametrize("starter", ["start_goal_sweep", "start_run_recovery"])
def test_bad_slot_settings_skip_ai_sweeps_and_say_why(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    starter: str,
) -> None:
    from pitch_coach_backend import main

    started: list[bool] = []

    async def stop_nothing() -> None:
        return None

    def start() -> Any:
        started.append(True)
        return stop_nothing

    monkeypatch.setattr(main, starter, start)
    monkeypatch.setattr(settings, "agent_enabled", True)
    _slots(monkeypatch, per_process=1, processes=1, total=4)

    async def run_app() -> None:
        async with main.lifespan(main.app):
            pass

    with caplog.at_level(logging.ERROR, logger="pitch_coach_backend"):
        asyncio.run(run_app())

    assert started == []
    assert any("CHAT_AI_SLOTS_PER_PROCESS" in record.getMessage() for record in caplog.records)
