import hashlib
import json
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from pitch_coach_backend.core.ai_http import SCHEMA_MAJOR
from pitch_coach_backend.core.validation import reject_unstorable_json
from pitch_coach_backend.module.chat.dto import ActionType, AgentTrigger

MAX_LINE_BYTES = 2 * 1024 * 1024
MAX_DIAGNOSTICS_BYTES = 65_536

PRE_STREAM_ERROR_CODES = frozenset(
    {"AGENT_DISABLED", "AGENT_BUSY", "AGENT_STATE_INVALID", "AGENT_CONTEXT_TOO_LARGE"}
)
STREAM_ERROR_CODES = frozenset({"AGENT_OUTPUT_TOO_LARGE", "AGENT_TIMEOUT"})
UPSTREAM_ERROR = "AGENT_UPSTREAM_ERROR"

TERMINAL_EVENTS = frozenset({"final", "error"})


class InvalidTurnReply(ValueError):
    pass


# 기초 BaseModel인 _View에 대한 설정
class _View(BaseModel):
    model_config = ConfigDict(strict=True, allow_inf_nan=False, extra="ignore")


class StreamLine(_View):
    event: Literal["plan", "tool_done", "claim", "final", "error"]
    run_id: str
    seq: int = Field(ge=1)
    data: dict[str, Any]


class _Error(_View):
    code: str
    message: str


class _ErrorBody(_View):
    error: _Error


class _Claim(_View):
    kind: Literal["RECORD", "GUIDE", "GENERAL", "ACTION_RESULT", "SYSTEM", "ASSESSMENT"]
    text: str = Field(min_length=1, max_length=300)


class _Message(_View):
    text: str
    claims: list[_Claim]
    cards: list[dict[str, Any]]
    sources: list[dict[str, Any]]


class _SignedState(_View):
    v: Literal[1]
    kid: str
    payload: str
    signature: str


class _Preapproved(_View):
    by: Literal["CHOICE"]
    proposal_id: str
    content_hash: str


def args_hash(kind: str, args: dict[str, Any]) -> str:
    canonical = json.dumps(
        {"type": kind, "args": args}, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class _Action(_View):
    action_id: str
    type: ActionType
    args: dict[str, Any]
    autonomy: Literal["AUTO", "ASK", "BUTTON"]
    args_hash: str | None
    idempotency_key: str
    reason: str
    authorized_by: str | None
    preapproved: _Preapproved | None = None

    @model_validator(mode="after")
    def _hash_matches_args(self) -> Self:
        if self.autonomy == "ASK" and self.args_hash != args_hash(self.type, self.args):
            raise ValueError("args_hash 가 args 로 계산한 값과 다르다")
        return self


class _Budget(_View):
    model_calls: int = Field(ge=0)
    tool_calls: int = Field(ge=0)
    elapsed_ms: int = Field(ge=0)
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)


class _GoalResult(_View):
    goal_version: int = Field(ge=0)
    take_id: str
    review_revision: int = Field(ge=0)
    status: Literal["ACHIEVED", "NOT_ACHIEVED", "UNKNOWN", "BASELINE_MISMATCH"]
    observed: float | None
    reason: str
    terminal: bool

    @model_validator(mode="after")
    def _matches_status(self) -> Self:
        if self.terminal == (self.status == "UNKNOWN"):
            raise ValueError("terminal 은 UNKNOWN 일 때만 false 다")
        return self


class _Meta(_View):
    schema_version: str
    feature_version: str
    model: str | None
    prompt_version: str
    corpus_version: str
    criteria_versions: dict[str, Any]


class FinalView(_View):
    status: Literal["DONE", "NEEDS_ACTION", "PARTIAL", "REFUSED"]
    message: _Message | None
    actions: list[_Action]
    state: _SignedState | None
    scope_pitch_id: str | None
    budget_used: _Budget
    assessments: list[dict[str, Any]] | None = None
    goal_result: _GoalResult | None = None
    meta: _Meta

    @model_validator(mode="after")
    def _matches_status(self) -> Self:
        if any(
            action.preapproved is not None
            and (action.type != "SAVE_GOAL" or action.autonomy != "ASK")
            for action in self.actions
        ):
            raise ValueError("preapproved 는 ASK 인 SAVE_GOAL 에만 실을 수 있다")
        if self.status == "NEEDS_ACTION":
            if len(self.actions) != 1 or self.actions[0].type == "START_PRACTICE":
                raise ValueError("NEEDS_ACTION 은 기다리는 행동 하나가 필요하다")
            if self.state is None:
                raise ValueError("NEEDS_ACTION 은 state 가 필요하다")
            action = self.actions[0]
            if action.autonomy == "ASK":
                if self.message is None:
                    raise ValueError("ASK 행동 응답은 message 가 필요하다")
                if action.preapproved is None and not any(
                    card.get("type") == "ASK_CONFIRM" and card.get("action_id") == action.action_id
                    for card in self.message.cards
                ):
                    raise ValueError("승인을 기다리는 행동에 ASK_CONFIRM 카드가 없다")
        else:
            if self.state is not None or self.message is None:
                raise ValueError("종결 응답은 message 가 필요하고 state 가 없다")
            allowed = 1 if self.status == "DONE" else 0
            if len(self.actions) > allowed or any(
                action.type != "START_PRACTICE" for action in self.actions
            ):
                raise ValueError("종결 응답에 실을 수 없는 행동")
        if self.message is not None and self.message.text != "\n".join(
            claim.text for claim in self.message.claims
        ):
            raise ValueError("message.text 가 claims 와 다르다")
        return self


class _Args(BaseModel):
    model_config = ConfigDict(strict=True, allow_inf_nan=False, extra="forbid")


class FetchPitchArgs(_Args):
    pitch_id: str | None
    name: str | None

    @model_validator(mode="after")
    def _exactly_one(self) -> Self:
        if (self.pitch_id is None) == (self.name is None):
            raise ValueError("pitch_id 와 name 중 정확히 하나가 필요하다")
        if self.name is not None and not self.name.strip():
            raise ValueError("name 은 공백만으로 둘 수 없다")
        return self


class FetchTakeArgs(_Args):
    pitch_id: str
    take_id: str


class ReassessArgs(_Args):
    take_id: str
    change: dict[str, Any]

    @model_validator(mode="after")
    def _one_allowed_key(self) -> Self:
        if len(self.change) != 1 or next(iter(self.change)) not in ("script_used", "targets"):
            raise ValueError("change 는 허용 키 하나만 가진다")
        return self


class StartPracticeArgs(_Args):
    pitch_id: str


class SaveGoalArgs(_Args):
    expected_goal_version: int = Field(ge=0)
    goal: dict[str, Any]


ACTION_ARGS: dict[str, type[_Args]] = {
    "FETCH_PITCH": FetchPitchArgs,
    "FETCH_TAKE": FetchTakeArgs,
    "REASSESS": ReassessArgs,
    "SAVE_GOAL": SaveGoalArgs,
}
ACTION_AUTONOMY = {
    "FETCH_PITCH": "AUTO",
    "FETCH_TAKE": "AUTO",
    "REASSESS": "ASK",
    "SAVE_GOAL": "ASK",
}


def _reject_constant(value: str) -> Any:
    raise ValueError(f"JSON 에 {value} 를 쓸 수 없다")


def _loads(raw: bytes) -> Any:
    value = json.loads(raw.decode("utf-8"), parse_constant=_reject_constant)
    reject_unstorable_json(value)
    return value


class StreamReader:
    def __init__(self, run_id: str) -> None:
        self._run_id = run_id
        self._buffer = bytearray()
        self._seq = 0
        self._terminal: StreamLine | None = None

    # NDJSON Stream을 읽고, StreamLine 객체 리스트를 반환
    def feed(self, chunk: bytes) -> list[StreamLine]:
        self._buffer += chunk
        lines = []
        while (end := self._buffer.find(b"\n")) >= 0:
            raw = bytes(self._buffer[: end + 1])
            del self._buffer[: end + 1]
            lines.append(self._line(raw))
        if len(self._buffer) >= MAX_LINE_BYTES:
            raise InvalidTurnReply("스트림 한 줄이 상한을 넘었다")
        return lines

    def finish(self) -> StreamLine:
        if self._buffer or self._terminal is None:
            raise InvalidTurnReply("끝 줄 없이 스트림이 끝났다")
        return self._terminal

    # StreamLine으로 한 줄 변환
    def _line(self, raw: bytes) -> StreamLine:
        if len(raw) > MAX_LINE_BYTES:
            raise InvalidTurnReply("스트림 한 줄이 상한을 넘었다")
        if self._terminal is not None:
            raise InvalidTurnReply("끝 줄 뒤에 줄이 더 왔다")
        try:
            line = StreamLine.model_validate(_loads(raw))
        except (ValueError, TypeError) as exc:
            raise InvalidTurnReply("스트림 줄 형식이 틀렸다") from exc
        if line.run_id != self._run_id or line.seq != self._seq + 1:
            raise InvalidTurnReply("다른 실행이거나 순번이 어긋난 줄")
        self._seq = line.seq
        if line.event in TERMINAL_EVENTS:
            self._terminal = line
        return line


def pre_stream_error_code(body: bytes) -> str:
    try:
        code = _ErrorBody.model_validate(_loads(body)).error.code
    except ValueError, TypeError:
        return UPSTREAM_ERROR
    return code if code in PRE_STREAM_ERROR_CODES else UPSTREAM_ERROR


def stream_error_code(data: dict[str, Any]) -> str:
    try:
        code = _ErrorBody.model_validate(data).error.code
    except ValueError, TypeError:
        return UPSTREAM_ERROR
    return code if code in STREAM_ERROR_CODES else UPSTREAM_ERROR


def validate_final(data: dict[str, Any], trigger: AgentTrigger) -> FinalView:
    try:
        view = FinalView.model_validate(data)
        if view.meta.schema_version.split(".", 1)[0] != SCHEMA_MAJOR:
            raise ValueError("지원하지 않는 응답 버전")
        if trigger == "USER_MESSAGE" and view.status != "NEEDS_ACTION" and view.assessments is None:
            raise ValueError("사용자 말로 시작한 실행의 답에 판단이 없다")
        if (
            trigger == "TAKE_REVIEWED"
            and view.status != "NEEDS_ACTION"
            and (view.goal_result is None or view.actions)
        ):
            raise ValueError("자동 목표 확인 답에 goal_result 가 없거나 행동이 있다")
    except (ValueError, TypeError) as exc:
        raise InvalidTurnReply("최종 출력이 계약과 다르다") from exc
    return view


def valid_diagnostics(
    value: Any, *, request_id: str, run_id: str, turn_call: int
) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    size = len(json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8"))
    if size > MAX_DIAGNOSTICS_BYTES:
        return None
    if (
        value.get("v") != 1
        or value.get("request_id") != request_id
        or value.get("run_id") != run_id
        or value.get("turn_call") != turn_call
        or not isinstance(value.get("steps"), list)
    ):
        return None
    return value
