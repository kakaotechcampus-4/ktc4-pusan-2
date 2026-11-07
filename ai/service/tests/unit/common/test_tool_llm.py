"""도구 호출 LLM(`common/llm.py` 의 `ToolLlmClient`) 시험.

공급자 대신 가짜 스트림으로 조립 · 재시도 · 실패 분류를 본다(대화 기능 문서 6-11 · 8-3).
연구 모듈 chat_agent_lab/llm.py 의 시험을 옮겼다."""

from __future__ import annotations

import time
from types import SimpleNamespace as NS
from typing import Any

import httpx2
import openai
import pytest

from pitch_coach_ai.common.llm import ToolLlmClient, ToolRole
from pitch_coach_ai.features.chat_agent.llm_types import (
    LlmBudgetStop,
    LlmCallFailed,
    LlmInvalidOutput,
    LlmRefused,
    LlmRequest,
    LlmTimeout,
    LlmUnavailable,
)


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
    resp = httpx2.Response(
        code,
        headers=headers,
        request=httpx2.Request("POST", "http://gateway.test/v1/chat/completions"),
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


#: 연구 모듈의 역할 상수와 같은 값(서비스는 settings.LLM_ROLES['chat_agent'] + LLM_TIMEOUT_SECONDS)
ROLE = ToolRole(max_output_tokens=2000, reasoning_effort="low", timeout_seconds=12.0)


def make(outcomes: list[Any]) -> tuple[ToolLlmClient, FakeCompletions, list[float]]:
    sleeps: list[float] = []
    llm = ToolLlmClient(
        base_url="http://gateway.test/v1",
        api_key="test",
        model="openai/gpt-6-luna",
        role=ROLE,
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
        # 같은 키가 두 번이면 무효(흘려 낸 앞 값과 다 받은 파싱의 뒤 값이 갈라지지 않게, 5-4)
        ([chunk(content='{"a":1,"b":{"c":1,"c":2}}'), chunk(finish="stop")], LlmInvalidOutput),
        ([chunk(finish="stop")], LlmInvalidOutput),
        ([chunk(content="{}"), chunk(finish="weird")], LlmInvalidOutput),
    ],
)
def test_invalid_or_refused_output_is_not_retried(chunks: list[NS], exc: type) -> None:
    """빈 답 · 잘림 · 형식 오류 · 모르는 finish_reason 은 같은 호출을 다시 보내지 않는다(8-3).

    거절은 REFUSED 로 구분한다."""
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

    from pitch_coach_ai.features.chat_agent.llm_types import LlmTimeout

    gate = threading.Event()
    llm, fake, _ = make([])
    llm._role = ToolRole(max_output_tokens=2000, reasoning_effort="low", timeout_seconds=0.3)  # type: ignore[attr-defined]
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

    llm, fake, _ = make([])
    llm._role = ToolRole(max_output_tokens=2000, reasoning_effort="low", timeout_seconds=0.2)  # type: ignore[attr-defined]
    fake.outcomes = [SlowStream(0.35, [chunk(content="{}"), chunk(finish="stop")])]
    fake.create = lambda **kw: (fake.kwargs.append(kw), fake.outcomes.pop(0))[1]  # type: ignore[method-assign]
    r = llm.invoke(req(tools=False), always)
    assert r.parsed == {} and len(fake.kwargs) == 1


# ── 응답 조각을 받는 쪽(문장 줄, 5-4) ───────────────────────────────────────────


class CutStream(Stream):
    """조각 몇 개를 낸 뒤 연결이 끊기는 스트림."""

    def __iter__(self) -> Any:
        yield from self.chunks
        raise httpx2.ReadError("끊김")


class Sink:
    def __init__(self, *, commit_after: int | None) -> None:
        self.commit_after = commit_after
        self.starts: list[int] = []
        self.feeds: list[tuple[int, str]] = []

    def start(self, attempt: int) -> None:
        self.starts.append(attempt)

    def feed(self, attempt: int, text: str) -> None:
        self.feeds.append((attempt, text))

    def committed(self) -> bool:
        return self.commit_after is not None and len(self.feeds) >= self.commit_after


def sink_req(sink: Sink) -> LlmRequest:
    import dataclasses

    return dataclasses.replace(req(tools=False), text_sink=sink)


def test_chunks_go_to_the_sink_with_the_attempt_number() -> None:
    llm, _, _ = make([[chunk(content='{"a":'), chunk(content="1}"), chunk(finish="stop")]])
    sink = Sink(commit_after=None)
    r = llm.invoke(sink_req(sink), always)
    assert r.parsed == {"a": 1}
    assert sink.starts == [1] and sink.feeds == [(1, '{"a":'), (1, "1}")]


def test_call_that_already_showed_a_sentence_is_not_resent() -> None:
    """화면에 낸 문장이 있으면 끊겨도 다시 보내지 않는다(낸 문장이 다른 초안과 섞이지 않게)."""
    llm, fake, sleeps = make(
        [CutStream([chunk(content='{"claims":[')]), [chunk(content="{}"), chunk(finish="stop")]]
    )
    sink = Sink(commit_after=1)
    with pytest.raises(LlmCallFailed):
        llm.invoke(sink_req(sink), always)
    assert len(fake.kwargs) == 1 and sleeps == []
    assert sink.starts == [1]


def test_call_that_showed_nothing_is_resent_and_the_sink_restarts() -> None:
    llm, fake, _ = make(
        [CutStream([chunk(content='{"claims":[')]), [chunk(content="{}"), chunk(finish="stop")]]
    )
    sink = Sink(commit_after=None)
    r = llm.invoke(sink_req(sink), always)
    assert r.attempts == 2 and len(fake.kwargs) == 2
    assert sink.starts == [1, 2] and sink.feeds[-1] == (2, "{}")


# ── 서비스에서 더한 것: 역할 · 만들기 · 끊김 예외 · 같은 키 · 로그 ─────────────────────────────


def test_tool_role_reads_the_chat_agent_role_and_call_timeout() -> None:
    """역할 `chat_agent` 은 settings.LLM_ROLES(출력 2,000 · 추론 low)와
    LLM_TIMEOUT_SECONDS(12초)를 쓴다."""
    from pitch_coach_ai.common.llm import tool_role

    assert tool_role() == ROLE


def test_tool_llm_is_none_without_a_model_bundle() -> None:
    """묶음이 없으면 None — 코어가 스트림 전 503 LLM_UNAVAILABLE 로 답한다(키 없는 CI · 로컬)."""
    from pitch_coach_ai.common.llm import tool_llm
    from pitch_coach_ai.settings import Settings

    assert tool_llm(pool_size=8, app_settings=Settings(_env_file=None)) is None


def test_tool_llm_builds_a_client_with_the_bundle_and_pool_size() -> None:
    from pitch_coach_ai.common.llm import tool_llm
    from pitch_coach_ai.settings import Settings

    s = Settings(
        _env_file=None,
        openai_base_url="http://gateway.test/v1",
        openai_api_key="test",
        openai_model="openai/gpt-6-luna",
    )
    llm = tool_llm(pool_size=3, app_settings=s)
    assert isinstance(llm, ToolLlmClient)
    assert (llm._model, llm._pool_size, llm._role) == ("openai/gpt-6-luna", 3, ROLE)  # type: ignore[union-attr]


def test_transport_error_before_the_stream_is_retried() -> None:
    """스트림을 열기 전 전송 오류(httpx2 예외)도 다시 부를 수 있는 연결 실패다."""

    class Fake(FakeCompletions):
        def create(self, **kwargs: Any) -> Any:
            if not self.kwargs:
                self.kwargs.append(kwargs)
                raise httpx2.ConnectError("연결 실패")
            return super().create(**kwargs)

    llm, _, _ = make([])
    fake = Fake([[chunk(content="{}"), chunk(finish="stop")]])
    llm._client = NS(chat=NS(completions=fake))  # type: ignore[assignment]
    r = llm.invoke(req(tools=False), always)
    assert r.attempts == 2 and r.attempt_errors == ["LLM_ERROR"]


def test_fm_L1_46_duplicate_top_level_key_is_invalid_output() -> None:
    """구조화 출력에서 같은 키가 두 번이면 무효 출력.

    흘려 낸 앞 값과 파싱한 뒤 값이 갈라지지 않게 한다(L1-46)."""
    llm, fake, _ = make([[chunk(content='{"route": "A", "route": "B"}'), chunk(finish="stop")]])
    with pytest.raises(LlmInvalidOutput) as ei:
        llm.invoke(req(tools=False), always)
    assert ei.value.cause == "json" and len(fake.kwargs) == 1


def test_log_line_has_counts_but_no_prompt_or_answer(log_records: list[Any]) -> None:
    """기본 로그는 서비스 로거 한 줄(역할 · 목적 · 모델 · 시도 · 결과 · 토큰 · 시간).

    프롬프트 · 답은 남기지 않는다."""
    llm = ToolLlmClient(
        base_url="http://gateway.test/v1", api_key="test", model="openai/gpt-6-luna", role=ROLE
    )
    fake = FakeCompletions(
        [[chunk(content='{"route": "비밀 답"}'), chunk(finish="stop"), chunk(usage=(120, 30))]]
    )
    llm._client = NS(chat=NS(completions=fake))  # type: ignore[assignment]
    llm.invoke(req(tools=False), always)
    lines = [r.getMessage() for r in log_records if r.getMessage().startswith("llm_call ")]
    assert len(lines) == 1
    line = lines[0]
    assert "role=chat_agent purpose=ANSWER" in line and "outcome=ok" in line
    assert "tokens_in=120 tokens_out=30" in line
    assert "안녕" not in line and "비밀 답" not in line


def test_read_timeout_mid_stream_is_a_timeout_and_retried_only_before_showing() -> None:
    """스트림 도중 시간 초과(httpx2.ReadTimeout)는 LlmTimeout 이다.

    화면에 낸 것이 없을 때만 다시 보낸다."""
    cut = httpx2.ReadTimeout("느림")
    llm, fake, _ = make(
        [CutStream([chunk(content='{"a"')]), [chunk(content="{}"), chunk(finish="stop")]]
    )
    fake.outcomes[0].__class__ = type(
        "TimeoutStream", (CutStream,), {"__iter__": lambda s: _cut_with(s, cut)}
    )
    r = llm.invoke(sink_req(Sink(commit_after=None)), always)
    assert r.attempts == 2 and r.attempt_errors == ["LLM_TIMEOUT"]

    llm, fake, sleeps = make(
        [CutStream([chunk(content='{"a"')]), [chunk(content="{}"), chunk(finish="stop")]]
    )
    fake.outcomes[0].__class__ = type(
        "TimeoutStream", (CutStream,), {"__iter__": lambda s: _cut_with(s, cut)}
    )
    with pytest.raises(LlmTimeout) as ei:
        llm.invoke(sink_req(Sink(commit_after=1)), always)
    assert ei.value.cause == "stream_timeout" and len(fake.kwargs) == 1 and sleeps == []


def _cut_with(stream: Any, exc: Exception) -> Any:
    yield from stream.chunks
    raise exc


def test_tool_llm_is_none_for_an_unknown_role(log_records: list[Any]) -> None:
    from pitch_coach_ai.common.llm import tool_llm

    assert tool_llm("no_such_role", pool_size=2) is None
    assert any("cause=UnknownRole" in r.getMessage() for r in log_records)


def test_tool_llm_build_failure_is_none_and_logs_only_the_class(
    monkeypatch: pytest.MonkeyPatch, log_records: list[Any]
) -> None:
    """클라이언트를 만들다 예외가 나면 None 이다.

    로그에는 예외 종류만 남는다(메시지에 설정값이 섞일 수 있다)."""
    from pitch_coach_ai.common import llm as llm_module
    from pitch_coach_ai.settings import Settings

    def boom(**_kw: Any) -> Any:
        raise ValueError("secret-key-sk-123")

    monkeypatch.setattr(llm_module.openai, "OpenAI", boom)
    s = Settings(
        _env_file=None,
        openai_base_url="http://gateway.test/v1",
        openai_api_key="test",
        openai_model="openai/gpt-6-luna",
    )
    assert llm_module.tool_llm(pool_size=2, app_settings=s) is None
    msgs = [r.getMessage() for r in log_records]
    assert any("llm_build_failed" in m and "cause=ValueError" in m for m in msgs)
    assert not any("secret-key" in m for m in msgs)
    # 예외 원문을 traceback 으로도 남기지 않는다
    assert all(r.exc_info is None for r in log_records)


def test_queued_call_that_timed_out_before_starting_is_cancelled_and_resent() -> None:
    """풀이 차서 시작도 못 한 호출이 시간 초과되면 취소한다.

    보내지 않음이 확실하므로 다시 보낼 수 있다."""
    import threading
    from concurrent.futures import ThreadPoolExecutor

    gate = threading.Event()
    pool = ThreadPoolExecutor(max_workers=1)
    pool.submit(gate.wait)  # 자리 하나를 막아 둔다
    try:
        llm = ToolLlmClient(
            base_url="http://gateway.test/v1",
            api_key="test",
            model="openai/gpt-6-luna",
            role=ToolRole(max_output_tokens=2000, reasoning_effort="low", timeout_seconds=0.2),
            executor=pool,
            pool_size=1,
            sleep=lambda _s: gate.set(),  # 다시 보내기 전 대기에서 자리를 풀어 준다
        )
        fake = FakeCompletions([[chunk(content="{}"), chunk(finish="stop")]])
        llm._client = NS(chat=NS(completions=fake))  # type: ignore[assignment]
        r = llm.invoke(req(tools=False), always)
        assert r.attempts == 2 and r.attempt_errors == ["LLM_TIMEOUT"] and len(fake.kwargs) == 1
        # 취소된 호출 · 끝난 재시도 모두 자리를 돌려줬다(풀 크기 1 이라 하나라도 남으면 포화)
        assert llm._inflight == 0 and not llm.saturated()
    finally:
        gate.set()
        pool.shutdown(wait=True)


def test_closed_pool_returns_the_slot_and_fails_without_sending() -> None:
    from concurrent.futures import ThreadPoolExecutor

    pool = ThreadPoolExecutor(max_workers=1)
    pool.shutdown()
    llm = ToolLlmClient(
        base_url="http://gateway.test/v1",
        api_key="test",
        model="openai/gpt-6-luna",
        role=ROLE,
        executor=pool,
        pool_size=1,
    )
    with pytest.raises(LlmCallFailed) as ei:
        llm.invoke(req(tools=False), always)
    assert ei.value.cause == "executor_closed" and not llm.saturated()
