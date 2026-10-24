"""BE <-> FE WebSocket 메시지. 텍스트 프레임은 전부 JSON 이고 type 으로 구분한다.

오디오는 텍스트가 아니라 binary 프레임이다 (event_ingestion.py 의 헤더 형식).
필드 이름은 REST DTO 와 같은 snake_case.
"""

import json
import uuid
from enum import StrEnum
from typing import Annotated, Any, Literal, get_args

from pydantic import AfterValidator, BaseModel, Field, TypeAdapter, model_validator

from pitch_coach_backend.core.validation import reject_unstorable_json

# metrics 의 Take 시각 상한. 코치 AI 가 받는 `t_ms` 상한과 같다 (Take 최대 1시간 + 60초 여유,
# `docs/ai/architecture.md` 1-4)
MAX_METRICS_T_MS = 3_660_000
# `take_metric_seconds.slide_number` 는 PostgreSQL integer(int4). 넘으면 저장에서 터지고, 실패한
# 행이 버퍼에 남아 같은 묶음의 정상 행까지 계속 못 넣는다
MAX_SLIDE_NUMBER = 2_147_483_647
# 시선 · 음량 1초 기록 하나의 JSON 크기 상한(바이트). 실제 기록은 200바이트 안팎이다. Take 하나가
# 저장 못 한 기록을 600초까지 메모리에 들고 있으므로 크기를 묶는다
MAX_SECOND_RECORD_BYTES = 4_096

# ── FE -> BE ──────────────────────────────────────────────────────────


class AuthMessage(BaseModel):
    """연결 후 첫 메시지. 브라우저 WebSocket 은 Authorization 헤더를 못 붙인다."""

    type: Literal["auth"]
    token: str


class StopMessage(BaseModel):
    """Take 종료. BE 가 Deepgram 을 정리하고 stt_status=closed 를 보낼 때까지 FE 는 닫지 않는다."""

    type: Literal["stop"]


def _second_record(value: dict[str, Any]) -> dict[str, Any]:
    """시선 · 음량 1초 기록. 창을 고를 때 읽는 시작 시각 · 길이만 보고 나머지는 그대로 둔다."""
    for key in ("t_ms", "duration_ms"):
        v = value.get(key)
        if isinstance(v, bool) or not isinstance(v, int | float) or v < 0:
            raise ValueError(f"{key} 는 0 이상의 숫자여야 합니다.")
    if len(json.dumps(value, ensure_ascii=False).encode()) > MAX_SECOND_RECORD_BYTES:
        raise ValueError(f"1초 기록은 {MAX_SECOND_RECORD_BYTES}바이트 이하여야 합니다.")
    # 원자료로 JSONB 에 그대로 들어간다
    return reject_unstorable_json(value)


SecondRecord = Annotated[dict[str, Any], AfterValidator(_second_record)]


class MetricsMessage(BaseModel):
    """1초 측정값 (시선 · 음량 1초 기록, 지금 슬라이드 번호). 잴 수 없는 값은 null 이다.

    FE 는 1초마다 보내고, `stop` 직전에 마지막 1초 조각을 실제 길이로 한 번 더 보낸다. `t_ms` 는
    그 1초가 끝난 Take 시각이고 `gaze.t_ms` · `voice.t_ms` 는 시작 시각이다. 끊긴 동안 쌓아
    두었다가 다시 붙으면 보내므로 같은 1초가 또 올 수 있다 (`docs/ai/architecture.md` 1-4).
    """

    type: Literal["metrics"]
    t_ms: int = Field(ge=0, le=MAX_METRICS_T_MS)
    gaze: SecondRecord | None
    voice: SecondRecord | None
    slide_number: Annotated[int, Field(ge=1, le=MAX_SLIDE_NUMBER)] | None

    @model_validator(mode="after")
    def _records_end_at_t_ms(self) -> MetricsMessage:
        # 두 기록은 바깥 t_ms 에서 끝나는 그 1초다 (시작 + 길이 = 끝). 어긋난 기록을 받으면 복구
        # (replay)가 그 1초를 엉뚱한 창에 넣는다
        for name, record in (("gaze", self.gaze), ("voice", self.voice)):
            if record is not None and record["t_ms"] + record["duration_ms"] != self.t_ms:
                raise ValueError(f"{name} 의 t_ms + duration_ms 는 바깥 t_ms 와 같아야 합니다.")
        return self


ClientMessage = Annotated[AuthMessage | StopMessage | MetricsMessage, Field(discriminator="type")]
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
    - `TAKE_ENDED` 는 ANALYZING·COMPLETED·FAILED 이거나 BE 가 자동 종료한 Take(`AutoEndMessage`).
      끝난 연습에 전사를 더 붙이면 리포트가 오염된다. BE 가 끝냈으면 `end_reason` 에 이유를 싣는다.
    - `FORBIDDEN` 은 소유권 검사(DB) 뒤의 이중 안전장치라 정상 흐름에서는 나오지 않는다.
    """

    UNAUTHORIZED = "UNAUTHORIZED"
    TAKE_NOT_FOUND = "TAKE_NOT_FOUND"
    TAKE_ENDED = "TAKE_ENDED"
    FORBIDDEN = "FORBIDDEN"
    BAD_MESSAGE = "BAD_MESSAGE"
    BAD_AUDIO_FRAME = "BAD_AUDIO_FRAME"
    TAKE_TAKEN_OVER = "TAKE_TAKEN_OVER"


# BE 가 Take 를 끝낸 이유 (`takes.end_reason` 과 같은 값). `TAKE_ENDED` 에 실어 FE 가 분기한다.
#   SILENCE · TIME_LIMIT : 자동 종료 (`AutoEndReason`)
#   ABANDONED            : 창을 닫지 않고 끊긴 채 돌아오지 않았다. FE 가 STT 없이 발표를 이어
#                          가는 중일 수 있다 — 발표를 마치고 끝내기 PUT 을 보내면 되살아난다.
#                          첫 연결에 오면 시작하지 않은 채 끝난 Take 다 (새 Take 를 만듦 · 10분)
#   CLOSED               : 창을 닫았다. 이어 가지 않는다
TakeEndReason = Literal["SILENCE", "TIME_LIMIT", "ABANDONED", "CLOSED"]
TAKE_END_REASONS: tuple[str, ...] = get_args(TakeEndReason)


class AutoEndReason(StrEnum):
    """BE 가 Take 를 스스로 끝낸 이유. FE 가 분기하는 값이다."""

    SILENCE = "SILENCE"  # 오래 말이 없었다
    TIME_LIMIT = "TIME_LIMIT"  # 최대 시간이 지났다


class AutoEndMessage(BaseModel):
    """BE 가 Take 를 자동으로 끝낸다. 이 메시지 뒤로는 오디오를 받지 않는다.

    순서: `auto_end` → (남은 오디오 정리) → `stt_status` closed → 1000 close.
    FE 는 사용자가 끝내기를 누른 것과 같은 종료 흐름을 타고 `message` 를 보여 준다.
    `stop` 을 보내도 되고 안 보내도 된다 — 서버는 이미 정리하고 있다.
    같은 Take 로 다시 붙으면 이 메시지를 한 번 더 보내고 `TAKE_ENDED` 로 거절한다 (자동 종료 순간
    떨어져 있던 FE 도 종료 흐름을 타게).
    """

    type: Literal["auto_end"] = "auto_end"
    reason: AutoEndReason
    message: str


class ErrorMessage(BaseModel):
    """무엇이 잘못됐는지. 연결을 닫을지는 code 가 정한다 (WsErrorCode 표)."""

    type: Literal["error"] = "error"
    code: WsErrorCode
    message: str
    # `TAKE_ENDED` 일 때 BE 가 끝낸 이유. 사용자가 끝냈거나 다른 코드면 싣지 않는다
    end_reason: TakeEndReason | None = Field(default=None, exclude_if=lambda v: v is None)
