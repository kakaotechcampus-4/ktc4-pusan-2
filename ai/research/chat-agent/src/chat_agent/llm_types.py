"""코어가 부르는 도구 호출 LLM 의 모양 (기능 문서 6-11). 코어는 LLM 객체를 만들지 않고 인자로 받는다.

- 누가 만드나: 연구 모듈은 `chat_agent_lab/llm.py`(OpenAI 호환 스트리밍), 서비스 탑재 뒤에는 `common/llm.py`의
  `tool_llm`. 시험은 정해 둔 답을 차례로 내는 가짜 모델(tests/unit/fakes.py).
- 호출 결과는 도구 호출 · 구조화(`response_schema`) 모드 모두 같은 모양이다:
  `{message, tool_calls[{id, name, args}], finish_reason, model, usage{input_tokens, output_tokens}, attempts}`.
- 한도: 코어는 전송 시도마다 `before_attempt(n)`으로 예산을 원자적으로 예약한다(8-3). False 면 LLM 쪽은 보내지 않고
  `LlmBudgetStop`을 올린다. 그래서 내부 재시도도 모델 호출 수에 들어간다.
- 실패는 `LlmError` 하위 예외이고 `attempts` · `usage`(모르면 None)를 달고 온다. 공급자 거절은 `LlmRefused`.
- 응답 조각: 요청에 `text_sink`가 있으면 LLM 쪽은 시도마다 `start(n)`을 부르고 받은 글 조각을 `feed(n, 조각)`으로 넘긴다.
  받는 쪽이 화면에 낸 것이 생겼으면(`committed()`) 그 호출은 다시 보내지 않는다(5-4 문장 줄 · 6-11).
- 서비스로 옮길 때 `common/errors.py`의 같은 이름(`LlmTimeout` 등)과 맞춘다.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

Purpose = Literal["RESEARCH", "ANSWER", "FIX"]


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    #: 파싱한 인자. 파싱하지 못했으면 None 이고 raw_args 에 원문(잘린 출력 등)
    args: dict[str, Any] | None
    raw_args: str | None = None


@dataclass(frozen=True)
class Usage:
    input_tokens: int | None = None
    output_tokens: int | None = None
    cached_input_tokens: int | None = None


class TextSink(Protocol):
    """구조화 호출의 응답 조각을 받는 쪽(답 쓰기의 문장 줄, 5-4). LLM 쪽은 다른 스레드에서 부를 수 있다."""

    def start(self, attempt: int) -> None:
        """새 전송 시도가 시작된다. 앞 시도의 조각은 버린다."""

    def feed(self, attempt: int, text: str) -> None:
        """시도 `attempt`의 응답 조각. 지난 시도(시간 초과 뒤 늦게 온 것)의 조각은 받는 쪽이 버린다."""

    def committed(self) -> bool:
        """이 호출에서 화면에 낸 것이 있는가. 있으면 LLM 쪽은 다시 보내지 않는다(낸 문장이 다른 초안과 섞이지 않게)."""


@dataclass(frozen=True)
class LlmRequest:
    purpose: Purpose
    messages: list[dict[str, Any]]
    #: 도구 모드면 도구 정의(OpenAI function 모양), 구조화 모드면 None
    tools: list[dict[str, Any]] | None = None
    #: 구조화 모드면 JSON Schema(이름 · 스키마)
    response_schema: dict[str, Any] | None = None
    max_output_tokens: int = 2_000
    #: 마감(monotonic 초). 넘기면 보내지 않는다
    deadline: float = 0.0
    #: 이 호출 안의 최대 시도 수(고치기는 1)
    max_attempts: int = 2
    #: 응답 조각을 받는 쪽(답 쓰기만). 없으면 조각을 넘기지 않는다
    text_sink: TextSink | None = None


@dataclass(frozen=True)
class LlmResult:
    message: str | None
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: str = "stop"
    model: str | None = None
    usage: Usage = field(default_factory=Usage)
    attempts: int = 1
    #: 구조화 모드에서 파싱한 JSON(실패면 None)
    parsed: dict[str, Any] | None = None
    #: 시도마다의 사용량(실패한 앞 시도 포함, 마지막이 이 결과). 비면 `usage` 하나로 본다(5-5 · L1-39)
    attempt_usages: list[Usage] = field(default_factory=list)
    #: 실패한 앞 시도의 오류 코드(진단 기록 MODEL 단계, 11-5)
    attempt_errors: list[str] = field(default_factory=list)


class LlmError(Exception):
    code = "LLM_ERROR"

    def __init__(
        self, *, attempts: int = 1, usage: list[Usage] | None = None, cause: str = ""
    ) -> None:
        super().__init__(cause or self.code)
        self.attempts = attempts
        #: 시도마다의 사용량(모르면 Usage(None, None))
        self.usage = usage or []
        self.cause = cause


class LlmTimeout(LlmError):
    code = "LLM_TIMEOUT"


class LlmCallFailed(LlmError):
    code = "LLM_ERROR"


class LlmInvalidOutput(LlmError):
    code = "LLM_INVALID_OUTPUT"


class LlmUnavailable(LlmError):
    code = "LLM_UNAVAILABLE"


class LlmRefused(LlmError):
    code = "LLM_REFUSED"


class LlmBudgetStop(LlmError):
    """before_attempt 가 False 를 돌려 보내지 않았다(한도 · 시간)."""

    code = "LIMIT"


class ToolLlm(Protocol):
    #: 응답한 모델 이름(meta.model). 아직 성공한 호출이 없으면 None
    model_name: str | None

    def invoke(self, req: LlmRequest, before_attempt: Callable[[int], bool]) -> LlmResult: ...
