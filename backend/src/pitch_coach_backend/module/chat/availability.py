"""대화 기능을 쓸 수 있는지. 대화 API · 사용 가능 여부 · 뜰 때 띄우는 주기 작업이 모두 이것을 본다.

끄기 스위치(`AGENT_ENABLED`)가 켜져 있어도 대화 자리 설정이 어긋나면 꺼진 것과 같다
(core/ai_slots.py `chat_slots_problem`, chat-agent.md L2-13). 설정은 뜰 때 한 번 읽고 바뀌지
않으므로 뜬 동안 결과도 같다.
"""

import logging

from pitch_coach_backend.core import ai_slots
from pitch_coach_backend.core.config import settings

logger = logging.getLogger(__name__)


def chat_enabled() -> bool:
    return settings.agent_enabled and ai_slots.chat_slots_problem() is None


def check_on_startup() -> bool:
    """뜰 때 부른다. 스위치를 켰는데 자리 설정 때문에 꺼지면 까닭을 남긴다."""
    if settings.agent_enabled and (problem := ai_slots.chat_slots_problem()):
        logger.error("대화 자리 설정이 어긋나 대화 기능을 끈다: %s", problem)
    return chat_enabled()
