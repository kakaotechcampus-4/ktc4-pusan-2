"""Depends 로 주입하는 것. 둘 다 프로세스에 하나다.

전사 저장소는 요청 밖(끝내기 PUT 의 뒤처리, `live_takes.py`)에서도 `get_transcript_store()` 로
꺼내 쓴다. 그래서 테스트는 Depends 가 아니라 이 객체(`_transcript_store`)를 가짜로 바꿔 끼운다.
"""

from pitch_coach_backend.core.config import settings
from pitch_coach_backend.realtime.storage.store import DbTranscriptStore, TranscriptStore
from pitch_coach_backend.realtime.stt.adapter import DeepgramSttAdapter, SttAdapter

# 연결 풀이 없다. 세션마다 새 WebSocket 을 여니 어댑터는 키와 키별 소진 표시만 들고 있다.
# 모든 Take 가 이 하나를 같이 쓰므로 한 Take 에서 소진된 키는 다른 Take 도 건너뛴다
_deepgram = DeepgramSttAdapter(settings.deepgram_keys)
# 저장할 때마다 짧은 DB 세션을 여니 저장소도 상태가 없다
_transcript_store = DbTranscriptStore()


def get_stt_adapter() -> SttAdapter:
    return _deepgram


def get_transcript_store() -> TranscriptStore:
    return _transcript_store
