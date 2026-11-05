import uuid
from typing import Annotated, Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from pitch_coach_backend.core.validation import NulFreeStr


class ChatInfoDTO(BaseModel):
    chat_id: str
    user_id: str
    message: str
    timestamp: str


AgentTrigger = Literal["USER_MESSAGE", "ACTION_RESULT", "TAKE_REVIEWED"]
ActionType = Literal["FETCH_PITCH", "FETCH_TAKE", "REASSESS", "SAVE_GOAL", "START_PRACTICE"]
ActionStatus = Literal["SUCCEEDED", "FAILED", "AMBIGUOUS"]
ProfileKey = Literal["TONE", "EXPLAIN_STYLE", "FOCUS_AREA", "CONTEXT"]
SummaryKind = Literal["TOPIC", "INTENT", "DECISION", "OPEN"]

Count = Annotated[int, Field(ge=0)]
HistoryText = Annotated[str, Field(max_length=4000)]


class _AgentDTO(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class AgentRunDTO(_AgentDTO):
    run_id: uuid.UUID
    conversation_id: uuid.UUID
    user_id: uuid.UUID
    scope_pitch_id: uuid.UUID | None
    trigger: AgentTrigger
    state_revision: Count


class AgentChoiceDTO(_AgentDTO):
    proposal_id: uuid.UUID
    option: Literal["USER", "EXPERT"]


class AgentReviewEventDTO(_AgentDTO):
    take_id: uuid.UUID
    review_revision: Count
    goal_version: Count


class AgentPageDTO(_AgentDTO):
    route: str
    pitch_id: uuid.UUID | None
    take_id: uuid.UUID | None
    screen: dict[str, Any] | None


class AgentHistoryMessageDTO(_AgentDTO):
    role: Literal["user", "assistant", "system"]
    text: HistoryText
    truncated: bool
    basis: dict[str, Any] | None
    stale_reasons: list[str]


class AgentSummaryItemDTO(_AgentDTO):
    kind: SummaryKind
    text: Annotated[str, Field(min_length=1, max_length=120)]


class AgentOverflowMessageDTO(_AgentDTO):
    role: Literal["user", "assistant", "system"]
    text: HistoryText


class AgentOverflowDTO(_AgentDTO):
    messages: list[AgentOverflowMessageDTO] = Field(min_length=1, max_length=10)
    prev_summary_version: Count


class AgentHistoryDTO(_AgentDTO):
    summary: Annotated[list[AgentSummaryItemDTO], Field(max_length=12)] | None
    messages: list[AgentHistoryMessageDTO] = Field(max_length=20)
    prior_assessments: list[dict[str, Any]] = Field(max_length=50)
    overflow: AgentOverflowDTO | None


class AgentContextDTO(_AgentDTO):
    pitches: list[dict[str, Any]] = Field(max_length=20)
    pitches_total: Count
    pitch: dict[str, Any] | None
    conversation_goal: dict[str, Any] | None
    goal_version: Count
    loaded_takes: list[dict[str, Any]]
    removed_sources: list[uuid.UUID]
    selected_proposal: dict[str, Any] | None


class SignedStateDTO(_AgentDTO):
    v: Literal[1]
    kid: str
    payload: str
    signature: str


class AgentErrorDTO(_AgentDTO):
    code: str
    message: str


class ActionResultDTO(_AgentDTO):
    action_id: str
    type: ActionType
    status: ActionStatus
    data: dict[str, Any] | None
    error: AgentErrorDTO | None


class AgentResumeDTO(_AgentDTO):
    state: SignedStateDTO
    action_results: list[ActionResultDTO] = Field(min_length=1, max_length=1)


class BudgetUsedDTO(_AgentDTO):
    model_calls: Count = 0
    tool_calls: Count = 0
    elapsed_ms: Count = 0
    input_tokens: Count = 0
    output_tokens: Count = 0


class AgentProfileItemDTO(_AgentDTO):
    item_id: uuid.UUID
    key: ProfileKey
    value: Annotated[str, Field(min_length=1, max_length=80)]


class AgentMemoryDTO(_AgentDTO):
    enabled: bool
    memory_version: Count
    profile: Annotated[list[AgentProfileItemDTO], Field(max_length=4)] | None

    @model_validator(mode="after")
    def _profile_when_enabled(self) -> Self:
        if not self.enabled and self.profile is not None:
            raise ValueError("memory.enabled 가 false 면 profile 은 null 이다")
        return self


class AgentTurnRequestDTO(_AgentDTO):
    run: AgentRunDTO
    user_message: NulFreeStr | None
    choice: AgentChoiceDTO | None
    review_event: AgentReviewEventDTO | None
    page: AgentPageDTO
    history: AgentHistoryDTO
    context: AgentContextDTO
    resume: AgentResumeDTO | None
    budget_used: BudgetUsedDTO
    memory: AgentMemoryDTO

    @model_validator(mode="after")
    def _matches_trigger(self) -> Self:
        trigger = self.run.trigger
        message = self.user_message
        if trigger == "USER_MESSAGE":
            if message is None or not message.strip() or len(message) > 2000:
                raise ValueError("USER_MESSAGE 는 1~2,000자 사용자 말이 필요하다")
            if self.resume is not None or self.review_event is not None:
                raise ValueError("USER_MESSAGE 에는 resume · review_event 를 싣지 않는다")
            if self.choice is not None and self.context.selected_proposal is None:
                raise ValueError("choice 가 있으면 selected_proposal 이 필요하다")
            return self
        if message is not None or self.choice is not None:
            raise ValueError(f"{trigger} 에는 사용자 말 · choice 를 싣지 않는다")
        if trigger == "ACTION_RESULT":
            if self.resume is None or self.review_event is not None:
                raise ValueError("ACTION_RESULT 는 resume 만 싣는다")
        elif self.review_event is None or self.resume is not None:
            raise ValueError("TAKE_REVIEWED 는 review_event 만 싣는다")
        return self
