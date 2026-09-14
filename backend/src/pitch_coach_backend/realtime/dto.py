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
    """인증이 끝났다. 이제 오디오를 보내도 된다.

    Deepgram 연결을 기다리지 않는다 — 아직 붙기 전이면 오디오는 큐에 쌓이고 `stt_state` 가
    `connecting` 으로 온다. STT 가 죽어 있어도 발표는 진행되어야 하기 때문이다.
    재연결로 기존 스트림에 다시 붙은 경우에는 그 시점의 상태와 세션 번호가 그대로 온다.
    """

    type: Literal["ready"] = "ready"
    take_id: uuid.UUID
    stt_session_no: int
    stt_state: SttState


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


# connecting 첫 연결 전 · ok 정상 · reconnecting 세션이 끊겨 재접속 중
# degraded 재접속이 이어서 실패 (재시도는 계속한다) · closed 정리 완료
SttState = Literal["connecting", "ok", "reconnecting", "degraded", "closed"]


class SttStatusMessage(BaseModel):
    """서버 2단 코치가 살아 있는지. 상태가 바뀔 때와 연결이 붙을 때 보낸다.

    숫자는 **Take 누적**이다 (연결 단위가 아니다). `lost_ms` 는 STT 에 닿지 못한 오디오 —
    채우지 못한 갭과 Deepgram 이 죽어 있는 동안 버린 프레임을 합친 값이다.
    """

    type: Literal["stt_status"] = "stt_status"
    state: SttState
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
