"""common/llm.py: 시간 제한 · 재시도 · 요청 마감 · 실패 분류 · 응답 모델 기록 (가짜 LLM, 과금 없음).

시간 제한 테스트는 12초를 기다리지 않도록 시간 제한을 0.2초처럼 줄여서 같은 규칙을 본다.
"""

import logging
import time
from collections.abc import Callable
from typing import Any

import httpx2
import openai
import pytest
from fastapi import APIRouter
from fastapi.testclient import TestClient
from langchain_core.exceptions import OutputParserException
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, LLMResult
from langchain_openai.chat_models.base import OpenAIRefusalError
from openai.types.chat import ChatCompletion
from pydantic import BaseModel, ValidationError

from pitch_coach_ai.common import llm as llm_module
from pitch_coach_ai.common.errors import (
    LlmCallFailed,
    LlmInvalidOutput,
    LlmTimeout,
    LlmUnavailable,
)
from pitch_coach_ai.common.llm import StructuredLlm, deadline_after, structured_llm
from pitch_coach_ai.main import create_app
from pitch_coach_ai.settings import Settings


class Answer(BaseModel):
    answer: int


Step = Callable[[Any], Any]


class FakeRunnable:
    """`.invoke(input, config)` 를 정해 둔 순서(steps)대로 흉내 낸다."""

    def __init__(self, *steps: Step) -> None:
        self.steps = list(steps)
        self.calls = 0

    def invoke(self, input: Any, config: Any = None) -> Any:  # noqa: A002
        self.calls += 1
        return self.steps.pop(0)(config)


def ok(
    result: Any = None,
    *,
    model: str = "openai/gpt-6-luna",
    finish: str = "stop",
    delay: float = 0,
    streaming: bool = True,
) -> Step:
    """성공한 호출. 콜백(on_llm_end)에 응답 모델 · 토큰 수 · 끝난 이유를 넘긴다.

    streaming=True(서비스가 실제로 쓰는 방식)면 llm_output 이 비고 모델은 generation_info,
    토큰은 메시지 usage_metadata 에 실린다. False 면 비스트리밍 모양(llm_output)이다.
    """

    def step(config: Any) -> Any:
        time.sleep(delay)
        if streaming:
            message = AIMessage(
                "",
                usage_metadata={"input_tokens": 30, "output_tokens": 7, "total_tokens": 37},
            )
            generation = ChatGeneration(
                message=message, generation_info={"finish_reason": finish, "model_name": model}
            )
            llm_result = LLMResult(generations=[[generation]], llm_output={})
        else:
            llm_result = LLMResult(
                generations=[
                    [
                        ChatGeneration(
                            message=AIMessage(""), generation_info={"finish_reason": finish}
                        )
                    ]
                ],
                llm_output={
                    "model_name": model,
                    "token_usage": {"prompt_tokens": 30, "completion_tokens": 7},
                },
            )
        for callback in config["callbacks"]:
            callback.on_llm_end(llm_result)
        return result if result is not None else Answer(answer=2)

    return step


class FakeClock:
    """마감 계산에 쓰는 시계. `advance` 로만 흐른다 (CI 가 느려도 결과가 같다)."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def spend(clock: FakeClock, seconds: float, then: Step) -> Step:
    """가짜 시계로 `seconds` 를 쓴 뒤 `then` 을 한다."""

    def step(config: Any) -> Any:
        clock.advance(seconds)
        return then(config)

    return step


def fail(exc: BaseException, *, delay: float = 0) -> Step:
    def step(config: Any) -> Any:
        time.sleep(delay)
        raise exc

    return step


def make(
    *steps: Step,
    timeout: float = 0.2,
    retries: int = 1,
    deadline: float | None = None,
    clock: FakeClock | None = None,
) -> tuple[StructuredLlm, FakeRunnable]:
    fake = FakeRunnable(*steps)
    extra = {"clock": clock} if clock is not None else {}
    llm = StructuredLlm(
        fake,
        role="default",
        timeout_seconds=timeout,
        max_retries=retries,
        deadline=deadline,
        **extra,
    )
    return llm, fake


_REQ = httpx2.Request("POST", "https://llm.example/v1/chat/completions")


def _status(cls: type[openai.APIStatusError], code: int) -> openai.APIStatusError:
    return cls("status", response=httpx2.Response(code, request=_REQ), body=None)


def _validation_error() -> ValidationError:
    try:
        Answer.model_validate({"answer": "둘"})
    except ValidationError as exc:
        return exc
    raise AssertionError


# ── 성공과 응답 모델 기록 ───────────────────────────────────────────


@pytest.mark.parametrize("streaming", [True, False])
def test_model_and_tokens_are_read_from_streaming_and_plain_replies(
    streaming: bool, log_records: list[logging.LogRecord]
) -> None:
    llm, _ = make(ok(model="gpt-6-luna", streaming=streaming))
    llm.invoke("q")
    assert llm.responded_model == "gpt-6-luna"
    line = next(r.getMessage() for r in log_records if r.name == "pitch_coach_ai.llm")
    assert "tokens_in=30 tokens_out=7" in line


def test_success_returns_schema_object_and_records_responding_model() -> None:
    llm, fake = make(ok(model="openai/gpt-6-luna-2026-06"))
    assert llm.invoke("1+1?") == Answer(answer=2)
    assert llm.responded_model == "openai/gpt-6-luna-2026-06"
    assert fake.calls == 1


# ── 시간 제한 · 재시도 · 요청 마감 ─────────────────────────────────


def test_call_longer_than_timeout_is_cut_and_retried_once() -> None:
    llm, fake = make(ok(delay=1.0), ok(model="retry-model"), timeout=0.2)
    started = time.monotonic()
    assert llm.invoke("q") == Answer(answer=2)
    assert time.monotonic() - started < 1.0  # 첫 호출(1초)을 끝까지 기다리지 않고 끊었다
    assert fake.calls == 2
    assert llm.responded_model == "retry-model"


def test_retries_at_most_once_then_raises_timeout() -> None:
    llm, fake = make(ok(delay=1.0), ok(delay=1.0), ok(), timeout=0.2, retries=1)
    with pytest.raises(LlmTimeout):
        llm.invoke("q")
    assert fake.calls == 2


def test_no_new_call_when_remaining_time_is_shorter_than_timeout() -> None:
    clock = FakeClock()
    llm, fake = make(ok(), timeout=12, deadline=clock.now + 11.9, clock=clock)
    with pytest.raises(LlmTimeout):
        llm.invoke("q")
    assert fake.calls == 0


def test_retry_is_skipped_when_deadline_runs_out() -> None:
    # 마감 25초 · 시간 제한 12초: 첫 호출이 14초를 쓰고 실패하면
    # 남은 11초 < 12초라 다시 부르지 않는다
    clock = FakeClock()
    llm, fake = make(
        spend(clock, 14, fail(openai.APIConnectionError(request=_REQ))),
        ok(),
        timeout=12,
        deadline=clock.now + 25,
        clock=clock,
    )
    with pytest.raises(LlmCallFailed):  # 마지막 실패를 그대로 올린다
        llm.invoke("q")
    assert fake.calls == 1


def test_retry_happens_when_enough_time_remains() -> None:
    clock = FakeClock()
    llm, fake = make(
        spend(clock, 3, fail(openai.APIConnectionError(request=_REQ))),
        ok(),
        timeout=12,
        deadline=clock.now + 25,
        clock=clock,
    )
    assert llm.invoke("q") == Answer(answer=2)  # 남은 22초 >= 12초
    assert fake.calls == 2


def test_later_calls_share_the_same_deadline() -> None:
    """장별 · 단계별로 여러 번 부를 때도 같은 마감을 쓴다."""
    clock = FakeClock()
    llm, fake = make(spend(clock, 14, ok()), ok(), timeout=12, deadline=clock.now + 25, clock=clock)
    llm.invoke("1번째 장")
    with pytest.raises(LlmTimeout):
        llm.invoke("2번째 장")  # 남은 11초 < 12초
    assert fake.calls == 1


def test_wait_is_cut_at_the_deadline_not_after_it() -> None:
    """호출을 시작한 뒤에는 제출까지 쓴 시간만큼 덜 기다려, 마감을 넘겨 기다리지 않는다.

    시계를 읽을 때마다 0.2초씩 흐르게 한다. 마감 0.5초 · 시간 제한 0.3초면 시작 검사(남은 0.5초)는
    통과하고, 기다리기 직전에는 남은 시간이 0.1초라 0.1초만 기다린다. 시간 제한(0.3초)을 그대로
    기다리면 0.25초를 넘는다.
    """

    class TickingClock(FakeClock):
        def __call__(self) -> float:
            value = self.now
            self.advance(0.2)
            return value

    clock = TickingClock()
    deadline = clock.now + 0.5
    llm, _ = make(ok(delay=1.0), timeout=0.3, retries=0, deadline=deadline, clock=clock)
    started = time.monotonic()
    with pytest.raises(LlmTimeout):
        llm.invoke("q")
    assert time.monotonic() - started < 0.25


# ── 실패 분류: 코드와 재시도 여부 ───────────────────────────────────


@pytest.mark.parametrize(
    ("exc", "expected", "retried"),
    [
        (openai.APITimeoutError(request=_REQ), LlmTimeout, True),
        (openai.APIConnectionError(request=_REQ), LlmCallFailed, True),
        (_status(openai.RateLimitError, 429), LlmUnavailable, True),
        (_status(openai.InternalServerError, 503), LlmUnavailable, True),
        (_status(openai.InternalServerError, 500), LlmCallFailed, True),
        (_status(openai.AuthenticationError, 401), LlmCallFailed, False),
        # 스트리밍 도중의 전송 오류는 openai 예외로 감싸지지 않는다
        (httpx2.ReadTimeout("read", request=_REQ), LlmTimeout, True),
        (httpx2.RemoteProtocolError("closed", request=_REQ), LlmCallFailed, True),
        (OpenAIRefusalError("refused"), LlmInvalidOutput, False),
        (_status(openai.BadRequestError, 400), LlmCallFailed, False),
        (
            openai.LengthFinishReasonError(completion=ChatCompletion.model_construct(usage=None)),
            LlmInvalidOutput,
            False,
        ),
        (OutputParserException("not json"), LlmInvalidOutput, False),
        (_validation_error(), LlmInvalidOutput, False),
    ],
)
def test_failures_map_to_codes_and_retry_only_when_it_can_help(
    exc: BaseException, expected: type[Exception], retried: bool
) -> None:
    llm, fake = make(fail(exc), fail(exc))
    with pytest.raises(expected):
        llm.invoke("q")
    assert fake.calls == (2 if retried else 1)


def test_output_cut_at_token_limit_is_invalid_output_without_retry() -> None:
    """형식은 맞아 보여도 출력 상한에서 잘렸으면(finish_reason=length) 받지 않는다."""
    llm, fake = make(ok(finish="length"), ok())
    with pytest.raises(LlmInvalidOutput):
        llm.invoke("q")
    assert fake.calls == 1
    assert llm.responded_model is None


# ── structured_llm: 설정 · 역할 ─────────────────────────────────────


def _full_settings(**extra: object) -> Settings:
    return Settings(
        _env_file=None,
        openai_base_url="https://llm.example/v1",
        openai_api_key="sk-test",
        openai_model="openai/gpt-6-luna",
        **extra,
    )


def test_missing_bundle_is_llm_unavailable() -> None:
    with pytest.raises(LlmUnavailable):
        structured_llm(Answer, app_settings=Settings(_env_file=None))


def test_client_uses_role_limits_and_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    class SpyChatOpenAI:
        def __init__(self, **kwargs: Any) -> None:
            seen.update(kwargs)

        def with_structured_output(self, schema: type[BaseModel]) -> FakeRunnable:
            seen["schema"] = schema
            return FakeRunnable(ok())

    monkeypatch.setattr(llm_module, "ChatOpenAI", SpyChatOpenAI)
    deadline = deadline_after(25)
    llm = structured_llm(Answer, deadline=deadline, app_settings=_full_settings())
    assert llm._deadline == deadline  # 라우터가 만든 마감을 그대로 쓴다
    assert seen["model"] == "openai/gpt-6-luna"
    assert seen["max_completion_tokens"] == 4096  # LLM_ROLES["default"]
    assert seen["timeout"] == 12  # LLM_TIMEOUT_SECONDS 기본값
    assert seen["max_retries"] == 0  # 재시도는 StructuredLlm 이 마감을 보며 한다
    # 게이트웨이가 비스트리밍 출력 상한을 2,000토큰으로 막아 스트리밍으로 부른다
    assert seen["streaming"] is True
    assert seen["stream_usage"] is True
    assert seen["schema"] is Answer
    assert llm._timeout == 12
    assert llm._max_retries == 1


# ── 로그 ────────────────────────────────────────────────────────────


def test_call_log_has_model_tokens_and_request_id_but_not_prompt(
    log_records: list[logging.LogRecord],
) -> None:
    router = APIRouter()

    @router.post("/_probe/llm-call")
    def call() -> dict[str, str | None]:
        llm, _ = make(ok(model="openai/gpt-6-luna"))
        llm.invoke("비밀 대본 프롬프트")
        return {"model": llm.responded_model}

    app = create_app()
    app.include_router(router)
    with TestClient(app) as client:
        client.post("/_probe/llm-call", headers={"X-Request-ID": "r-llm"})

    lines = [r.getMessage() for r in log_records if r.name == "pitch_coach_ai.llm"]
    assert len(lines) == 1
    line = lines[0]
    for part in (
        "request_id=r-llm",
        "model=openai/gpt-6-luna",
        "outcome=ok",
        "tokens_in=30",
        "tokens_out=7",
    ):
        assert part in line
    assert "비밀 대본" not in " ".join(r.getMessage() for r in log_records)


def test_failed_attempts_are_logged_with_code(log_records: list[logging.LogRecord]) -> None:
    llm, _ = make(fail(openai.APITimeoutError(request=_REQ)), ok())
    llm.invoke("q")
    outcomes = [r.getMessage() for r in log_records if r.name == "pitch_coach_ai.llm"]
    assert "attempt=1 outcome=LLM_TIMEOUT cause=APITimeoutError" in outcomes[0]
    assert "attempt=2 outcome=ok" in outcomes[1]


def test_request_id_in_llm_log_cannot_forge_fields(log_records: list[logging.LogRecord]) -> None:
    """요청 ID 는 밖에서 온 값이다. 공백을 이스케이프해 LLM 로그에 가짜 필드를 끼우지 못한다."""
    router = APIRouter()

    @router.post("/_probe/llm-forge")
    def call() -> dict[str, str]:
        llm, _ = make(ok())
        llm.invoke("q")
        return {}

    app = create_app()
    app.include_router(router)
    with TestClient(app) as client:
        client.post("/_probe/llm-forge", headers={"X-Request-ID": "fake outcome=ok"})
    line = next(r.getMessage() for r in log_records if r.name == "pitch_coach_ai.llm")
    assert r"request_id=fake\x20outcome=ok " in line
