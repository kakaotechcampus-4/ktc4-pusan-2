"""BE <-> FE WebSocket 메시지. 텍스트 프레임은 전부 JSON 이고 type 으로 구분한다.

오디오는 텍스트가 아니라 binary 프레임이다 (event_ingestion.py 의 헤더 형식).
필드 이름은 REST DTO 와 같은 snake_case.
"""

import uuid
from typing import Annotated, Literal

from pydantic import BaseModel, Field, TypeAdapter

# ── FE -> BE ──────────────────────────────────────────────────────────


class AuthMessage(BaseModel):
    """연결 후 첫 메시지. 브라우저 WebSocket 은 Authorization 헤더를 못 붙인다."""

    type: Literal["auth"]
    token: str


class StopMessage(BaseModel):
    """Take 종료. BE 가 Deepgram 을 정리하고 stt_status=closed 를 보낼 때까지 FE 는 닫지 않는다."""

    type: Literal["stop"]


ClientMessage = Annotated[AuthMessage | StopMessage, Field(discriminator="type")]
client_message_adapter: TypeAdapter[ClientMessage] = TypeAdapter(ClientMessage)


# ── BE -> FE ──────────────────────────────────────────────────────────


class ReadyMessage(BaseModel):
    """인증과 Deepgram 연결이 끝났다. 이제 오디오를 보내도 된다."""

    type: Literal["ready"] = "ready"
    take_id: uuid.UUID
    stt_session_no: int


class WordOut(BaseModel):
    word: str
    punctuated_word: str
    start_ms: int
    end_ms: int
    confidence: float


class TranscriptMessage(BaseModel):
    """segment_id 가 같은 메시지는 같은 구간이다. FE 는 마지막 것으로 덮어쓴다.

    is_final=False 인 동안 단어와 타임스탬프가 계속 바뀐다. 화면 갱신에만 쓴다.
    타임스탬프는 Take 시작 = 0 기준 (Deepgram 세션 기준이 아니다).
    """

    type: Literal["transcript"] = "transcript"
    segment_id: str
    is_final: bool
    speech_final: bool
    start_ms: int
    end_ms: int
    text: str
    confidence: float
    words: list[WordOut]


class SttStatusMessage(BaseModel):
    type: Literal["stt_status"] = "stt_status"
    state: Literal["ok", "degraded", "reconnecting", "closed"]
    stt_session_no: int
    frames: int
    dropped_frames: int
    silence_ms: int
    lost_ms: int


class ErrorMessage(BaseModel):
    """REST 에러 응답과 같은 code 체계. 닫기 직전에 한 번 보낸다."""

    type: Literal["error"] = "error"
    code: str
    message: str
