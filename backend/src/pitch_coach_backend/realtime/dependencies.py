"""Depends 로 주입하는 것. 테스트는 get_stt_adapter 를 가짜로 바꿔 끼운다."""

from pitch_coach_backend.core.config import settings
from pitch_coach_backend.realtime.stt_adapter import DeepgramSttAdapter, SttAdapter

# 연결 풀이 없다. 세션마다 새 WebSocket 을 여니 어댑터는 키만 들고 있으면 된다
_deepgram = DeepgramSttAdapter(settings.deepgram_api_key)


def get_stt_adapter() -> SttAdapter:
    return _deepgram
