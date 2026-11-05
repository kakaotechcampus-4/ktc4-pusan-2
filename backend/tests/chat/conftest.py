import uuid

import pytest


@pytest.fixture
def chat_app(monkeypatch: pytest.MonkeyPatch) -> list[uuid.UUID]:
    """대화를 켜고 턴 실행기를 막는다. 띄우려 한 run_id 를 모아 돌려준다.

    턴 실행기를 직접 시험하는 파일도 이 폴더에 있어 autouse 로 두지 않는다.
    """
    from pitch_coach_backend.core.config import settings
    from pitch_coach_backend.module.chat import controller

    monkeypatch.setattr(settings, "agent_enabled", True)
    started: list[uuid.UUID] = []
    monkeypatch.setattr(controller.turn_runner, "start", started.append)
    monkeypatch.setattr(controller.turn_runner, "resume", lambda run_id: None)
    return started


@pytest.fixture
def resumed(chat_app: list[uuid.UUID], monkeypatch: pytest.MonkeyPatch) -> list[uuid.UUID]:
    """승인 뒤 이어 돌리려 한 run_id 를 모은다."""
    from pitch_coach_backend.module.chat import controller

    ids: list[uuid.UUID] = []
    monkeypatch.setattr(controller.turn_runner, "resume", ids.append)
    return ids
