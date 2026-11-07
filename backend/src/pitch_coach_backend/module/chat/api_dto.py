"""대화 API(화면 ↔ BE) 요청 · 응답. AI 와 주고받는 모양은 dto.py 에 따로 있다."""

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StrictBool, field_validator

from pitch_coach_backend.core.validation import NulFreeStr, reject_unstorable_json
from pitch_coach_backend.module.chat.dto import ProfileKey

# 사용자 말 길이. 유니코드 문자(코드 포인트) 수로 센다. 넘으면 자르지 않고 422 다
MESSAGE_MAX_CHARS = 2000
MESSAGES_PAGE_MAX = 50
# 대화 목록 커서 길이. BE 가 만드는 값은 92자로 일정하다. 넘으면 풀어 보지 않고 거절한다
CONVERSATION_CURSOR_MAX_CHARS = 128
# 한 응답은 직렬화 뒤 2 MiB 이하다(12-3). 끝맺는 칸 몫을 남긴다
RESPONSE_MAX_BYTES = 2 * 1024 * 1024 - 4096

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


class ActionDecisionRequest(_Request):
    decision: Literal["APPROVE", "REJECT"]
    # 화면에 보인 카드의 값. 인자는 받지 않고 저장한 인자로만 실행한다
    args_hash: Annotated[NulFreeStr, Field(min_length=1, max_length=128)]


class MessageAcceptedResponse(BaseModel):
    conversation_id: uuid.UUID
    message_id: uuid.UUID
    run_id: uuid.UUID
    status: Literal["QUEUED"] = "QUEUED"


class CreateConversationRequest(_Request):
    pitch_id: uuid.UUID | None = None
    # '기억 없이' 대화. 만들 때만 정하고 바꾸지 않는다(6-14)
    memory_off: StrictBool = False


class ChangeScopeRequest(_Request):
    pitch_id: uuid.UUID | None


class ConversationResponse(BaseModel):
    conversation_id: uuid.UUID
    title: str
    scope_pitch_id: uuid.UUID | None
    scope_pitch_title: str | None
    last_user_activity_at: datetime
    created_at: datetime
    memory_off: bool


class ConversationListItem(ConversationResponse):
    # 마지막 답이 지금 기록과 달라졌는지(6-8). 답이 없으면 false
    stale: bool


class ConversationListResponse(BaseModel):
    conversations: list[ConversationListItem]
    # 다음 쪽을 읽을 커서. 값을 해석하지 않고 그대로 보낸다. 없으면 끝까지 다 읽었다
    next_cursor: str | None


class RunErrorDTO(BaseModel):
    # AI 의 error.message 원문은 싣지 않는다. 화면 문구는 FE 가 code 로 고른다
    code: str


class RunEventDTO(BaseModel):
    # 실행 전체에서 이어지는 순번. FE 는 마지막으로 처리한 값을 after_seq 로 보낸다
    event_seq: int
    event: Literal["plan", "tool_done", "claim"]
    # 줄 내용 그대로다. claim 은 {index, claim, evidence, sources, assessments}.
    # 근거가 지워졌으면 null 이고 masked 가 true 다
    data: dict[str, Any] | None
    masked: bool


class RunStatusResponse(BaseModel):
    run_id: uuid.UUID
    conversation_id: uuid.UUID
    status: RunStatus
    plan: Any | None
    trace: Any | None
    # after_seq 뒤의 실행 이벤트(순번순). SSE 를 붙여도 같은 모양이다
    events: list[RunEventDTO]
    # 다음 조회에 보낼 커서. 이벤트를 읽지 않은 응답(취소 · 승인 · 거절)은 null 이다
    next_after_seq: int | None
    # 크기 한도로 나눠 남은 이벤트가 있다. 기다리지 않고 next_after_seq 로 바로 다시 조회한다
    has_more: bool
    # 승인 대기 중인 확인 카드(ASK_CONFIRM). 그 밖에는 빈 목록이다
    cards: list[dict[str, Any]]
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
    # 이 답을 지금 다시 물으면 달라질 수 있는 이유(6-8). 저장하지 않고 읽을 때 계산한다
    stale_reasons: list[str]


class MessageListResponse(BaseModel):
    messages: list[ChatMessageResponse]
    # 더 앞의 메시지를 읽을 커서. 없으면 첫 메시지까지 다 읽었다
    next_before_seq: int | None


class GoalUndoneResponse(BaseModel):
    run_id: uuid.UUID
    action_id: str
    # 되돌려서 새로 만든 목표 판 번호
    goal_version: int
    # 되살린 목표. 처음 저장을 되돌렸으면 목표 내용이 없어 null
    goal: dict[str, Any] | None


class AvailabilityResponse(BaseModel):
    enabled: bool


class MemorySettingRequest(_Request):
    enabled: StrictBool


class MemoryItemResponse(BaseModel):
    item_id: uuid.UUID
    key: ProfileKey
    value: str


class MemorySettingResponse(BaseModel):
    enabled: bool
    items: list[MemoryItemResponse]
