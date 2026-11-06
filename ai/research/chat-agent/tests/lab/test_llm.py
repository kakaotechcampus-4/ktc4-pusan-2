"""연구용 도구 호출 LLM 함수(chat_agent_lab/llm.py) 시험 — 공급자 대신 가짜 스트림으로 조립 · 재시도 · 실패 분류를 본다 (6-11 · 8-3)."""

from __future__ import annotations

import time
from types import SimpleNamespace as NS
from typing import Any

import httpx
import openai
import pytest

from chat_agent.llm_types import (
    LlmBudgetStop,
    LlmCallFailed,
    LlmInvalidOutput,
    LlmRefused,
    LlmRequest,
    LlmUnavailable,
)
from chat_agent_lab.llm import OpenAiToolLlm


def chunk(
    *,
    content: str | None = None,
    tool: tuple[int, str | None, str | None, str | None] | None = None,
    finish: str | None = None,
    usage: tuple[int, int] | None = None,
) -> NS:
    tcs = None
    if tool is not None:
        idx, cid, name, args = tool
        tcs = [NS(index=idx, id=cid, function=NS(name=name, arguments=args))]
    choices = [NS(delta=NS(content=content, refusal=None, tool_calls=tcs), finish_reason=finish)]
    u = (
        NS(prompt_tokens=usage[0], completion_tokens=usage[1], prompt_tokens_details=None)
        if usage
        else None
    )
    return NS(model="openai/gpt-6-luna", choices=choices, usage=u)


class Stream:
    def __init__(self, chunks: list[NS]) -> None:
        self.chunks = chunks
        self.closed = False

    def __iter__(self) -> Any:
        return iter(self.chunks)

    def close(self) -> None:
        self.closed = True


def status_error(code: int, retry_after: str | None = None) -> openai.APIStatusError:
    headers = {"retry-after": retry_after} if retry_after else {}
    resp = httpx.Response(
        code,
        headers=headers,
        request=httpx.Request("POST", "http://gateway.test/v1/chat/completions"),
    )
    return openai.APIStatusError("오류", response=resp, body=None)


class FakeCompletions:
    def __init__(self, outcomes: list[Any]) -> None:
        self.outcomes = list(outcomes)
        self.kwargs: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> Any:
        self.kwargs.append(kwargs)
        out = self.outcomes.pop(0)
        if isinstance(out, Exception):
            raise out
        return Stream(out)


def make(outcomes: list[Any]) -> tuple[OpenAiToolLlm, FakeCompletions, list[float]]:
    sleeps: list[float] = []
    llm = OpenAiToolLlm(
        base_url="http://gateway.test/v1",
        api_key="test",
        model="openai/gpt-6-luna",
        sleep=sleeps.append,
    )
    fake = FakeCompletions(outcomes)
    llm._client = NS(chat=NS(completions=fake))  # type: ignore[assignment]
    return llm, fake, sleeps


TOOLS = [
    {"type": "function", "function": {"name": "compare_takes", "parameters": {"type": "object"}}}
]
SCHEMA = {"name": "x", "strict": True, "schema": {"type": "object"}}


def req(*, tools: bool = True, deadline_s: float = 60.0, attempts: int = 2) -> LlmRequest:
    return LlmRequest(
        purpose="RESEARCH" if tools else "ANSWER",
        messages=[{"role": "user", "content": "안녕"}],
        tools=TOOLS if tools else None,
        response_schema=None if tools else SCHEMA,
        deadline=time.monotonic() + deadline_s,
        max_attempts=attempts,
    )


def always(_n: int) -> bool:
    return True


def test_tool_call_deltas_are_joined_and_tool_mode_has_no_reasoning() -> None:
    llm, fake, _ = make(
        [
            [
                chunk(tool=(0, "call-1", "compare_", '{"previous_take_id":')),
                chunk(tool=(0, None, "takes", '"t-4","current_take_id":"t-5"}')),
                chunk(finish="tool_calls"),
                chunk(usage=(120, 30)),
            ]
        ]
    )
    r = llm.invoke(req(), always)
    assert [(c.id, c.name, c.args) for c in r.tool_calls] == [
        ("call-1", "compare_takes", {"previous_take_id": "t-4", "current_take_id": "t-5"})
    ]
    assert (r.usage.input_tokens, r.usage.output_tokens, r.attempts) == (120, 30, 1)
    kw = fake.kwargs[0]
    # 게이트웨이는 도구와 추론 강도를 함께 받지 않는다 → 도구 모드는 none, 한 번에 하나만
    assert kw["reasoning_effort"] == "none" and kw["parallel_tool_calls"] is False
    assert kw["stream_options"] == {"include_usage": True}


def test_schema_mode_parses_json_with_role_reasoning() -> None:
    llm, fake, _ = make(
        [[chunk(content='{"route":'), chunk(content=' "ANSWER"}'), chunk(finish="stop")]]
    )
    r = llm.invoke(req(tools=False), always)
    assert r.parsed == {"route": "ANSWER"}
    assert fake.kwargs[0]["reasoning_effort"] == "low"
    assert fake.kwargs[0]["response_format"]["type"] == "json_schema"


@pytest.mark.parametrize(
    ("chunks", "exc"),
    [
        ([chunk(content='{"a":'), chunk(finish="length")], LlmInvalidOutput),
        ([chunk(content="..."), chunk(finish="content_filter")], LlmRefused),
        ([chunk(content="not json"), chunk(finish="stop")], LlmInvalidOutput),
        ([chunk(finish="stop")], LlmInvalidOutput),
        ([chunk(content="{}"), chunk(finish="weird")], LlmInvalidOutput),
    ],
)
def test_invalid_or_refused_output_is_not_retried(chunks: list[NS], exc: type) -> None:
    """빈 답 · 잘림 · 형식 오류 · 모르는 finish_reason 은 같은 호출을 다시 보내지 않는다(8-3). 거절은 REFUSED 로 구분한다."""
    llm, fake, _ = make([chunks, [chunk(content="{}"), chunk(finish="stop")]])
    with pytest.raises(exc) as ei:
        llm.invoke(req(tools=False), always)
    assert len(fake.kwargs) == 1
    assert ei.value.attempts == 1


def test_503_retries_once_after_retry_after() -> None:
    llm, fake, sleeps = make(
        [status_error(503, "0.2"), [chunk(content="{}"), chunk(finish="stop")]]
    )
    r = llm.invoke(req(tools=False), always)
    assert r.attempts == 2 and len(fake.kwargs) == 2
    assert sleeps == [0.2]


def test_429_without_header_waits_half_second() -> None:
    llm, _, sleeps = make([status_error(429), [chunk(content="{}"), chunk(finish="stop")]])
    llm.invoke(req(tools=False), always)
    assert sleeps == [0.5]


def test_retry_skipped_when_wait_plus_call_does_not_fit() -> None:
    """대기 + 호출 한 번(12초)이 마감 안에 들어오지 않으면 다시 부르지 않는다(8-3)."""
    llm, fake, sleeps = make([status_error(503, "1"), [chunk(content="{}"), chunk(finish="stop")]])
    with pytest.raises(LlmUnavailable):
        llm.invoke(req(tools=False, deadline_s=10.0), always)
    assert len(fake.kwargs) == 1 and sleeps == []


@pytest.mark.parametrize("code", [400, 401, 403, 404])
def test_client_errors_are_not_retried(code: int) -> None:
    llm, fake, _ = make([status_error(code), [chunk(content="{}"), chunk(finish="stop")]])
    with pytest.raises(LlmCallFailed):
        llm.invoke(req(tools=False), always)
    assert len(fake.kwargs) == 1


def test_budget_refusal_sends_nothing() -> None:
    """시도마다 한도를 먼저 예약한다. 막히면 보내지 않고 LlmBudgetStop(8-3)."""
    llm, fake, _ = make([[chunk(content="{}"), chunk(finish="stop")]])
    with pytest.raises(LlmBudgetStop) as ei:
        llm.invoke(req(tools=False), lambda _n: False)
    assert fake.kwargs == [] and ei.value.attempts == 0


def test_second_attempt_also_reserves_budget() -> None:
    seen: list[int] = []

    def before(n: int) -> bool:
        seen.append(n)
        return n == 1

    llm, fake, _ = make([status_error(503, "0"), [chunk(content="{}"), chunk(finish="stop")]])
    with pytest.raises(LlmBudgetStop) as ei:
        llm.invoke(req(tools=False), before)
    assert seen == [1, 2] and len(fake.kwargs) == 1
    assert len(ei.value.usage) == 1  # 실패한 시도의 사용량(모름)도 남긴다


# ── 검증 반영: 끝났는지 모르는 시도 · 불완전한 호출 · 시도별 사용량 ─────────────


class BlockingStream:
    """닫아도 멈추지 않는 스트림(공급자가 취소를 무시하는 경우)."""

    def __init__(self, gate: Any) -> None:
        self.gate = gate

    def __iter__(self) -> Any:
        self.gate.wait(5)
        return iter([])

    def close(self) -> None:
        pass


def test_fm_L1_14_timed_out_attempt_not_confirmed_is_not_resent() -> None:
    """시간 초과된 앞 시도가 끝났는지 확인하지 못하면 같은 요청을 다시 보내지 않는다(6-11)."""
    import threading

    from chat_agent.llm_types import LlmTimeout
    from chat_agent_lab.llm import Role

    gate = threading.Event()
    llm, fake, _ = make([])
    llm._role = Role(max_output_tokens=2000, reasoning_effort="low", timeout_seconds=0.3)  # type: ignore[attr-defined]
    fake.outcomes = [BlockingStream(gate)]
    fake.create = lambda **kw: (fake.kwargs.append(kw), fake.outcomes.pop(0))[1]  # type: ignore[method-assign]
    try:
        with pytest.raises(LlmTimeout) as ei:
            llm.invoke(req(tools=False), always)
        assert ei.value.cause == "timeout_unconfirmed"
        assert len(fake.kwargs) == 1
    finally:
        gate.set()


def test_fm_L1_15_tool_call_without_id_is_invalid_output() -> None:
    llm, _, _ = make([[chunk(tool=(0, None, "get_screen", "{}")), chunk(finish="tool_calls")]])
    with pytest.raises(LlmInvalidOutput) as ei:
        llm.invoke(req(), always)
    assert ei.value.cause == "incomplete_tool_call"


def test_fm_L1_39_failed_attempt_usage_is_returned_with_success() -> None:
    """앞 시도가 실패하고 재시도가 성공해도 시도별 사용량(모르는 것 포함)을 함께 돌려준다."""
    llm, _, _ = make(
        [
            status_error(503, "0"),
            [chunk(content="{}"), chunk(finish="stop"), chunk(usage=(120, 30))],
        ]
    )
    r = llm.invoke(req(tools=False), always)
    assert [(u.input_tokens, u.output_tokens) for u in r.attempt_usages] == [
        (None, None),
        (120, 30),
    ]
    assert r.attempt_errors == ["LLM_UNAVAILABLE"]


class SlowStream:
    """마감은 넘기지만 기다리는 사이 끝나는 스트림."""

    def __init__(self, delay: float, chunks: list[NS]) -> None:
        self.delay = delay
        self.chunks = chunks

    def __iter__(self) -> Any:
        time.sleep(self.delay)
        return iter(self.chunks)

    def close(self) -> None:
        pass


def test_fm_L1_14_result_finished_right_after_timeout_is_used_not_resent() -> None:
    from chat_agent_lab.llm import Role

    llm, fake, _ = make([])
    llm._role = Role(max_output_tokens=2000, reasoning_effort="low", timeout_seconds=0.2)  # type: ignore[attr-defined]
    fake.outcomes = [SlowStream(0.35, [chunk(content="{}"), chunk(finish="stop")])]
    fake.create = lambda **kw: (fake.kwargs.append(kw), fake.outcomes.pop(0))[1]  # type: ignore[method-assign]
    r = llm.invoke(req(tools=False), always)
    assert r.parsed == {} and len(fake.kwargs) == 1
