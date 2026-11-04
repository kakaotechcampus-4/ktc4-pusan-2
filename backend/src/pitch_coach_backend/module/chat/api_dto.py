"""대화 API(화면 ↔ BE) 요청 · 응답. AI 와 주고받는 모양은 dto.py 에 따로 있다."""

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, field_validator

from pitch_coach_backend.core.validation import NulFreeStr, reject_unstorable_json

# 사용자 말 길이. 유니코드 문자(코드 포인트) 수로 센다. 넘으면 자르지 않고 422 다
MESSAGE_MAX_CHARS = 2000
MESSAGES_PAGE_MAX = 50

RunStatus = Literal[
    "QUEUED",
    "RUNNING",
    "WAITING_APPROVAL",
    "DONE",
    "PARTIAL",
    "REFUSED",
    "FAILED",
    "CANCELLED",
    "EXPIRED",
]


class _Request(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ChatPageDTO(_Request):
    """사용자가 보고 있던 화면. AI 요청의 `page` 로 그대로 간다."""

    route: Annotated[NulFreeStr, Field(min_length=1, max_length=64)]
    pitch_id: uuid.UUID | None = None
    take_id: uuid.UUID | None = None
    # 피치 만들기 · 수정 · 장치 점검 화면의 저장 전 입력.
    # 실행이 끝날 때까지만 실행의 원래 입력(JSONB)으로 둔다. 저장할 수 없는 값은 받을 때 422 다
    screen: Annotated[dict[str, Any], AfterValidator(reject_unstorable_json)] | None = None


class ChatChoiceDTO(_Request):
    """목표 제안 카드에서 고른 안."""

    proposal_id: uuid.UUID
    option: Literal["USER", "EXPERT"]


class SendMessageRequest(_Request):
    # 없으면 새 대화를 만든다. 그때만 `scope_pitch_id` 를 대화 범위로 쓴다.
    # 기존 대화의 범위는 범위 바꾸기 API 로 바꾼다
    conversation_id: uuid.UUID | None = None
    scope_pitch_id: uuid.UUID | None = None
    text: NulFreeStr
    page: ChatPageDTO
    # 사용자 단위로 유일하다(새 대화 만들기 포함). 같은 시도를 다시 보낼 때는 같은 값을 쓴다
    client_request_id: uuid.UUID
    again_of_message_id: uuid.UUID | None = None
    choice: ChatChoiceDTO | None = None

    @field_validator("text")
    @classmethod
    def _text_in_range(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("메시지가 비어 있어요.")
        if len(value) > MESSAGE_MAX_CHARS:
            raise ValueError(f"메시지는 {MESSAGE_MAX_CHARS}자까지 보낼 수 있어요.")
        return value


class MessageAcceptedResponse(BaseModel):
    conversation_id: uuid.UUID
    message_id: uuid.UUID
    run_id: uuid.UUID
    status: Literal["QUEUED"] = "QUEUED"


class CreateConversationRequest(_Request):
    pitch_id: uuid.UUID | None = None


class ChangeScopeRequest(_Request):
    pitch_id: uuid.UUID | None


class ConversationResponse(BaseModel):
    conversation_id: uuid.UUID
    title: str
    scope_pitch_id: uuid.UUID | None
    scope_pitch_title: str | None
    last_user_activity_at: datetime
    created_at: datetime


class ConversationListResponse(BaseModel):
    conversations: list[ConversationResponse]


class RunErrorDTO(BaseModel):
    # AI 의 error.message 원문은 싣지 않는다. 화면 문구는 FE 가 code 로 고른다
    code: str


class RunStatusResponse(BaseModel):
    run_id: uuid.UUID
    conversation_id: uuid.UUID
    status: RunStatus
    plan: Any | None
    trace: Any | None
    error: RunErrorDTO | None
    retry_after_s: int | None


class ChatMessageResponse(BaseModel):
    message_id: uuid.UUID
    seq: int
    role: Literal["user", "assistant", "system"]
    text: str
    status: str | None
    run_id: uuid.UUID | None
    again_of_message_id: uuid.UUID | None
    created_at: datetime
    claims: Any | None
    cards: Any | None
    assessments: Any | None
    evidence: Any | None
    sources: Any | None
    basis: Any | None
    plan: Any | None
    trace: Any | None
    # 근거가 된 피치 · Take 가 지워져 본문과 근거를 가렸다(사유 SOURCE_DELETED)
    masked: bool


class MessageListResponse(BaseModel):
    messages: list[ChatMessageResponse]
    # 더 앞의 메시지를 읽을 커서. 없으면 첫 메시지까지 다 읽었다
    next_before_seq: int | None


class AvailabilityResponse(BaseModel):
    enabled: bool
