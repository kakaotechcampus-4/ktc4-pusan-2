"""BE <-> FE WebSocket 메시지. 텍스트 프레임은 전부 JSON 이고 type 으로 구분한다.

오디오는 텍스트가 아니라 binary 프레임이다 (event_ingestion.py 의 헤더 형식).
필드 이름은 REST DTO 와 같은 snake_case.
"""

import uuid
from enum import StrEnum
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


class WsErrorCode(StrEnum):
    """FE 가 분기하는 값이다 (REST 의 에러 `code` 와 같은 역할).

    **에러가 곧 종료는 아니다.** 코드마다 연결을 어떻게 하는지가 다르다.

    | 코드 | 연결 |
    |---|---|
    | `UNAUTHORIZED` | 1008 로 닫는다 — 토큰이 없거나 틀리거나 사용자가 없다 |
    | `TAKE_NOT_FOUND` | 1008 로 닫는다 — Take 가 없거나 내 것이 아니다 |
    | `TAKE_ENDED` | 1008 로 닫는다 — 이미 끝난 Take. FE 는 **재연결하지 않는다** |
    | `FORBIDDEN` | 1008 로 닫는다 — 다른 사용자가 쓰고 있는 스트림 |
    | `BAD_MESSAGE` | 유지 |
    | `BAD_AUDIO_FRAME` | 유지. 연결당 첫 오류만 알린다 |
    | `TAKE_TAKEN_OVER` | 1008 로 닫는다. FE 는 **재연결하지 않는다** |

    - `TAKE_NOT_FOUND` 는 REST 의 404 와 같은 규칙으로 "없음" 과 "남의 것" 을 구분하지 않는다.
    - `TAKE_ENDED` 는 ANALYZING·COMPLETED·FAILED. 끝난 연습에 전사를 더 붙이면 리포트가 오염된다.
    - `FORBIDDEN` 은 소유권 검사(DB) 뒤의 이중 안전장치라 정상 흐름에서는 나오지 않는다.
    """

    UNAUTHORIZED = "UNAUTHORIZED"
    TAKE_NOT_FOUND = "TAKE_NOT_FOUND"
    TAKE_ENDED = "TAKE_ENDED"
    FORBIDDEN = "FORBIDDEN"
    BAD_MESSAGE = "BAD_MESSAGE"
    BAD_AUDIO_FRAME = "BAD_AUDIO_FRAME"
    TAKE_TAKEN_OVER = "TAKE_TAKEN_OVER"


class ErrorMessage(BaseModel):
    """무엇이 잘못됐는지. 연결을 닫을지는 code 가 정한다 (WsErrorCode 표)."""

    type: Literal["error"] = "error"
    code: WsErrorCode
    message: str
