"""대화 에이전트 엔드포인트 `POST /agent/turn` (`api/chat_agent.py`) — 서비스 계층 시험.

코어(`handle_turn`)의 규칙은 같은 폴더의 다른 시험이 본다. 여기서는 서비스 계층이 맡는 것을 본다
(기능 문서 4-1 · 6-11 · 11-1 · 7절의 '서비스 탑재 때' 행: L1-02 · L1-05 · L1-06 · L1-08 · L1-14 · L5-01).
- 끄기 스위치 · 서명 키 · 모델 설정 조합의 스트림 전 오류 본문(BE 파서가 읽는 그대로)
- 실행 자리(동시 상한 · 대화 전용 실행 풀)와 반납(스트림 끝 · 스트림 전 오류 · 연결 끊김)
- 본문 크기 한도(Content-Length 없음 · 청크 전송)
- NDJSON 스트림(헤더 · 줄마다 JSON · 끝 줄 하나), 요청 ID 가 진단 기록까지 이어짐
- 연결이 끊기거나 보내기가 막히면 새 모델 호출을 멈춤
- 자료 묶음은 검수를 마친 카드만(지금은 자료 없이 시작)
"""

from __future__ import annotations

import contextlib
import json
import logging
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import anyio
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from pitch_coach_ai.api import chat_agent as api
from pitch_coach_ai.main import create_app
from pitch_coach_ai.settings import Settings

from . import fakes as F
from .test_turn import COMPARE, MSG, change_draft, compare_answer

URL = "/agent/turn"
SECRET = SecretStr(F.SECRET.decode())


def _settings(**kw: Any) -> Settings:
    return Settings(_env_file=None, **kw)


def _runtime(llm: Any, *, max_concurrency: int = 4, secret: bytes | None = F.SECRET) -> Any:
    clock = F.Clock()
    events: list[dict[str, Any]] = []
    rt = api.AgentRuntime(
        llm=llm,
        corpus=None,
        secret=secret,
        previous_secret=None,
        max_concurrency=max_concurrency,
        clock_ms=clock.clock_ms,
        monotonic=clock.monotonic,
        log=events.append,
    )
    rt.events = events  # type: ignore[attr-defined]
    return rt


def _run_ended(rt: Any) -> bool:
    """코어 실행이 끝났다(agent_end 진단 기록). 이 뒤에는 새 모델 · 도구 호출이 없다."""
    return any(e.get("event") == "agent_end" for e in rt.events)


@pytest.fixture
def enabled_client() -> Iterator[TestClient]:
    with TestClient(create_app(_settings(agent_enabled=True))) as c:
        yield c


def _compare_request() -> dict[str, Any]:
    return F.with_loaded(F.request(user_message=MSG), "t-4")


def _compare_llm() -> F.FakeLlm:
    return F.FakeLlm(
        [F.plan(assessments=[change_draft()]), F.tools(COMPARE), F.done(), compare_answer()]
    )


def _post_stream(client: TestClient, body: Any, **kw: Any) -> tuple[int, dict[str, str], list[str]]:
    with client.stream("POST", URL, json=body, **kw) as r:
        lines = [ln for ln in r.iter_lines() if ln]
        return r.status_code, dict(r.headers), lines


def _wait(cond: Any, seconds: float = 3.0) -> bool:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.01)
    return cond()


# ── 스트림 전 오류: 스위치 · 서명 키 · 모델 (L1-05 · L5-01) ──────────────────────────


def test_disabled_is_503_agent_disabled_and_builds_nothing() -> None:
    with TestClient(create_app(_settings())) as c:
        r = c.post(URL, json=_compare_request())
        assert r.status_code == 503
        assert r.json()["error"]["code"] == "AGENT_DISABLED"
        assert set(r.json()["error"]) == {"code", "message"}
        assert getattr(c.app.state, "chat_agent_runtime", None) is None  # 모델 · 자료를 만들지 않음
        assert c.get("/health").status_code == 200


def test_existing_review_path_is_unchanged_when_chat_is_enabled(
    enabled_client: TestClient,
) -> None:
    """대화를 켜도(서명 키 · 모델 없음) 기존 리뷰는 기본 결과 200 그대로다(L5-01 · L5-33)."""
    from pitch_coach_ai.features.review import core as review_core

    req = json.loads(
        (Path(review_core.__file__).parent / "examples" / "review" / "request.json").read_text(
            encoding="utf-8"
        )
    )
    assert enabled_client.post(URL, json=_compare_request()).status_code == 503
    r = enabled_client.post("/takes/review", json=req)
    assert r.status_code == 200 and r.json()["fallback_reason"] == "LLM_UNAVAILABLE"


def test_enabled_without_signing_key_is_503_agent_disabled_only_on_the_chat_path(
    enabled_client: TestClient,
) -> None:
    r = enabled_client.post(URL, json=_compare_request())
    assert (r.status_code, r.json()["error"]["code"]) == (503, "AGENT_DISABLED")
    assert enabled_client.get("/health").status_code == 200


def test_enabled_with_key_but_no_model_bundle_is_503_llm_unavailable() -> None:
    s = _settings(agent_enabled=True, agent_state_secret=SECRET)
    with TestClient(create_app(s)) as c:
        r = c.post(URL, json=_compare_request())
        assert (r.status_code, r.json()["error"]["code"]) == (503, "LLM_UNAVAILABLE")
        assert c.app.state.chat_agent_runtime.active == 0


def test_short_signing_key_is_503_agent_disabled() -> None:
    s = _settings(agent_enabled=True, agent_state_secret=SecretStr("short"))
    with TestClient(create_app(s)) as c:
        r = c.post(URL, json=_compare_request())
        assert (r.status_code, r.json()["error"]["code"]) == (503, "AGENT_DISABLED")


def test_runtime_reads_both_signing_keys_and_sizes_the_model_pool() -> None:
    s = _settings(
        agent_enabled=True,
        agent_state_secret=SECRET,
        agent_state_secret_previous=SecretStr("p" * 32),
        agent_max_concurrency=3,
        openai_base_url="http://gateway.test/v1",
        openai_api_key="test",
        openai_model="openai/gpt-6-luna",
    )
    rt = api.AgentRuntime.from_settings(s)
    assert (rt.secret, rt.previous_secret) == (F.SECRET, b"p" * 32)
    assert rt.max_concurrency == 3 and rt.llm._pool_size == 6  # 대화 전용 실행 풀 = 상한 × 2


def test_blank_signing_key_in_env_is_none() -> None:
    assert _settings(agent_state_secret="").agent_state_secret is None


# ── 정상 스트림 ────────────────────────────────────────────────────────────────


def test_stream_is_ndjson_with_one_terminal_line_and_request_id_reaches_diagnostics(
    enabled_client: TestClient, log_records: list[logging.LogRecord]
) -> None:
    clock = F.Clock()
    rt = api.AgentRuntime(
        llm=_compare_llm(),
        corpus=None,
        secret=F.SECRET,
        previous_secret=None,
        max_concurrency=4,
        clock_ms=clock.clock_ms,
        monotonic=clock.monotonic,
    )  # 진단 기록은 서비스 로거(기본)로
    enabled_client.app.state.chat_agent_runtime = rt
    status, headers, lines = _post_stream(
        enabled_client, _compare_request(), headers={"X-Request-ID": "req-abc-1"}
    )
    assert status == 200
    assert headers["content-type"].startswith("application/x-ndjson")
    assert headers["x-request-id"] == "req-abc-1"
    assert headers["cache-control"] == "no-cache" and headers["x-accel-buffering"] == "no"
    events = [json.loads(x) for x in lines]
    assert [e["event"] for e in events][-1] == "final"
    assert sum(e["event"] in ("final", "error") for e in events) == 1
    assert events[-1]["data"]["diagnostics"]["request_id"] == "req-abc-1"
    assert _wait(lambda: rt.active == 0)
    msgs = [r.getMessage() for r in log_records if r.name == "pitch_coach_ai.chat_agent"]
    assert any(m.startswith("agent_stream request_id=req-abc-1 end=done") for m in msgs)
    # 진단 기록은 JSON 한 줄(본문 없음): 사용자 말은 남지 않는다
    diag = [json.loads(m) for m in msgs if m.startswith("{")]
    assert any(d.get("event") == "agent_end" for d in diag)
    assert all(MSG not in m for m in msgs)


def test_stream_runs_off_the_shared_thread_pool(enabled_client: TestClient) -> None:
    """코어 줄은 턴마다의 읽기 스레드가 넘긴다(공용 스레드 풀 이름 `AnyIO worker` 가 아님, 6-11 · 8-4)."""
    seen: list[str] = []
    llm = _compare_llm()
    inner = llm.invoke

    def spy(req: Any, before: Any) -> Any:
        seen.append(threading.current_thread().name)
        return inner(req, before)

    llm.invoke = spy  # type: ignore[method-assign]
    enabled_client.app.state.chat_agent_runtime = _runtime(llm)
    status, _, _ = _post_stream(enabled_client, _compare_request())
    assert status == 200 and seen
    assert all(not n.startswith("AnyIO worker") for n in seen)


# ── 실행 자리 (L1-06 · L1-14) ────────────────────────────────────────────────


def test_busy_when_all_turn_slots_are_taken(enabled_client: TestClient) -> None:
    rt = _runtime(_compare_llm(), max_concurrency=1)
    enabled_client.app.state.chat_agent_runtime = rt
    assert rt.try_acquire()
    try:
        r = enabled_client.post(URL, json=_compare_request())
        assert (r.status_code, r.json()["error"]["code"]) == (429, "AGENT_BUSY")
        assert "retry-after" not in r.headers  # Retry-After 는 BE 가 넣는다(5-9)
    finally:
        rt.release()
    assert rt.active == 0


def test_busy_when_the_model_pool_is_saturated(enabled_client: TestClient) -> None:
    llm = _compare_llm()
    llm.saturated = lambda: True  # type: ignore[attr-defined]
    rt = _runtime(llm)
    enabled_client.app.state.chat_agent_runtime = rt
    r = enabled_client.post(URL, json=_compare_request())
    assert (r.status_code, r.json()["error"]["code"]) == (429, "AGENT_BUSY")
    assert rt.active == 0 and llm.calls == []


def test_five_concurrent_entries_get_at_most_four_slots() -> None:
    """다섯 스레드가 장벽에서 동시에 자리를 얻으려 하면 정확히 넷만 얻는다(L1-06)."""
    rt = _runtime(_compare_llm(), max_concurrency=4)
    barrier = threading.Barrier(5)
    got: list[bool] = []
    lock = threading.Lock()

    def enter() -> None:
        barrier.wait()
        ok = rt.try_acquire()
        with lock:
            got.append(ok)

    threads = [threading.Thread(target=enter) for _ in range(5)]
    for th in threads:
        th.start()
    for th in threads:
        th.join(3)
    assert sorted(got) == [False, True, True, True, True] and rt.active == 4
    for _ in range(4):
        rt.release()
    assert rt.active == 0


@pytest.mark.parametrize(
    ("body", "status", "code"),
    [(b"{not json", 422, "VALIDATION_ERROR"), (b"[]", 422, "VALIDATION_ERROR")],
)
def test_pre_stream_errors_return_the_slot(
    enabled_client: TestClient, body: bytes, status: int, code: str
) -> None:
    rt = _runtime(_compare_llm())
    enabled_client.app.state.chat_agent_runtime = rt
    r = enabled_client.post(URL, content=body, headers={"content-type": "application/json"})
    assert (r.status_code, r.json()["error"]["code"]) == (status, code)
    assert rt.active == 0


# ── 본문 크기 (L1-02) ─────────────────────────────────────────────────────────


def test_body_over_two_mib_is_413_even_with_chunked_transfer(enabled_client: TestClient) -> None:
    rt = _runtime(_compare_llm())
    enabled_client.app.state.chat_agent_runtime = rt
    limit = rt.cfg.max_request_bytes

    def chunks() -> Iterator[bytes]:  # Content-Length 없이 보낸다
        yield b"{" + b" " * (limit // 2)
        yield b" " * (limit // 2 + 10) + b"}"

    r = enabled_client.post(URL, content=chunks(), headers={"content-type": "application/json"})
    assert (r.status_code, r.json()["error"]["code"]) == (413, "AGENT_CONTEXT_TOO_LARGE")
    assert rt.active == 0 and rt.llm.calls == []


def test_body_at_the_limit_is_read(enabled_client: TestClient) -> None:
    rt = _runtime(_compare_llm())
    enabled_client.app.state.chat_agent_runtime = rt
    body = json.dumps(_compare_request()).encode()
    padded = body[:-1] + b" " * (rt.cfg.max_request_bytes - len(body)) + b"}"
    assert len(padded) == rt.cfg.max_request_bytes
    status, _, lines = _post_stream(
        enabled_client, None, content=padded, headers={"content-type": "application/json"}
    )
    assert status == 200 and json.loads(lines[-1])["event"] == "final"


# ── 연결 끊김 · 보내기 막힘 (L1-08) ──────────────────────────────────────────────


def _slow_llm(entered: threading.Event, gate: threading.Event) -> F.FakeLlm:
    def slow_tools(_r: Any) -> Any:
        entered.set()
        gate.wait(3)
        return F.tools(COMPARE)

    return F.FakeLlm([F.plan(assessments=[change_draft()]), slow_tools, F.done(), compare_answer()])


def _asgi_scope(
    body: bytes, *, spec: str = "2.4", content_length: int | None = -1
) -> dict[str, Any]:
    headers = [(b"content-type", b"application/json")]
    if content_length is not None:
        n = len(body) if content_length == -1 else content_length
        headers.append((b"content-length", str(n).encode()))
    return {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": spec},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": URL,
        "raw_path": URL.encode(),
        "query_string": b"",
        "root_path": "",
        "headers": headers,
        "client": ("test", 1),
        "server": ("test", 80),
    }


@pytest.mark.parametrize("spec", ["2.3", "2.4"])  # uvicorn 은 2.3(Starlette 가 듣는다), 2.4 는 직접
def test_client_disconnect_stops_new_model_calls_and_returns_the_slot(spec: str) -> None:
    """첫 줄을 받은 뒤 연결이 끊기면 다음 줄을 보낼 때까지 기다리지 않고 새 모델 호출을 멈춘다(L1-08).

    ASGI 앱을 직접 불러 실제 연결 끊김 메시지(`http.disconnect`)를 보낸다(TestClient 는 끊김을 흉내 내지 않는다).
    """
    entered, gate = threading.Event(), threading.Event()
    llm = _slow_llm(entered, gate)
    rt = _runtime(llm)
    app = create_app(_settings(agent_enabled=True))
    app.state.chat_agent_runtime = rt
    body = json.dumps(_compare_request()).encode()
    sent: list[bytes] = []

    async def main() -> None:
        first_line = anyio.Event()
        state = {"read": False}

        async def send(message: Any) -> None:
            if message["type"] == "http.response.body" and message.get("body"):
                sent.append(message["body"])
                first_line.set()

        async def receive() -> Any:
            if not state["read"]:
                state["read"] = True
                return {"type": "http.request", "body": body, "more_body": False}
            await first_line.wait()
            await anyio.to_thread.run_sync(entered.wait, 3)  # 조사 호출 안에서 기다리는 중에 끊는다
            return {"type": "http.disconnect"}

        await app(_asgi_scope(body, spec=spec), receive, send)

    anyio.run(main)
    assert entered.is_set()  # 모델 호출 도중에 끊었다
    gate.set()
    assert json.loads(sent[0])["event"] == "plan"
    assert _wait(lambda: rt.active == 0 and _run_ended(rt))
    assert [c.purpose for c in llm.calls] == ["RESEARCH", "RESEARCH"]  # 답 쓰기를 부르지 않음


@pytest.mark.parametrize("spec", ["2.3", "2.4"])
def test_disconnect_before_the_first_line_returns_the_slot_without_answering(spec: str) -> None:
    """첫 줄(헤더)을 보내기 전에 끊겨도 새 호출을 멈추고 자리를 돌려준다(L1-08 헤더 전 단절)."""
    entered, gate = threading.Event(), threading.Event()

    def slow_plan(_r: Any) -> Any:
        entered.set()
        gate.wait(3)
        return F.plan(assessments=[change_draft()])

    llm = F.FakeLlm([slow_plan, F.tools(COMPARE), F.done(), compare_answer()])
    rt = _runtime(llm)
    app = create_app(_settings(agent_enabled=True))
    app.state.chat_agent_runtime = rt
    body = json.dumps(_compare_request()).encode()

    start_entered = threading.Event()

    async def main() -> None:
        state = {"read": False}
        started = anyio.Event()

        async def receive() -> Any:
            if not state["read"]:
                state["read"] = True
                return {"type": "http.request", "body": body, "more_body": False}
            await started.wait()  # 헤더를 보내는 중에 끊는다
            return {"type": "http.disconnect"}

        async def send(message: Any) -> None:
            if message["type"] == "http.response.start":
                start_entered.set()
                started.set()
                await anyio.to_thread.run_sync(entered.wait, 3)
                raise OSError("연결이 끊겨 헤더를 보내지 못함")

        with contextlib.suppress(Exception):
            await app(_asgi_scope(body, spec=spec), receive, send)

    anyio.run(main)
    assert start_entered.is_set() and entered.is_set()
    gate.set()
    assert _wait(lambda: rt.active == 0 and _run_ended(rt))
    assert [c.purpose for c in llm.calls] == ["RESEARCH"]


@pytest.mark.parametrize("content_length", [None, 10])  # 길이 없음 · 실제보다 작은 거짓 길이
def test_body_in_many_chunks_is_counted_not_trusted_from_content_length(
    content_length: int | None,
) -> None:
    rt = _runtime(_compare_llm())
    app = create_app(_settings(agent_enabled=True))
    app.state.chat_agent_runtime = rt
    limit = rt.cfg.max_request_bytes
    parts = [b"{" + b" " * (limit // 3)] * 4  # 넷째 조각에서 한도를 넘는다
    sent: list[dict[str, Any]] = []

    async def main() -> None:
        queue = list(parts)

        async def receive() -> Any:
            if queue:
                chunk = queue.pop(0)
                return {"type": "http.request", "body": chunk, "more_body": bool(queue)}
            return {"type": "http.disconnect"}

        async def send(message: Any) -> None:
            sent.append(message)

        await app(
            _asgi_scope(b"".join(parts), spec="2.3", content_length=content_length),
            receive,
            send,
        )

    anyio.run(main)
    assert sent[0]["status"] == 413
    body = json.loads(b"".join(m.get("body", b"") for m in sent[1:]))
    assert body["error"]["code"] == "AGENT_CONTEXT_TOO_LARGE"
    assert rt.active == 0 and rt.llm.calls == []


def test_send_blocked_over_two_seconds_cancels_the_turn(monkeypatch: pytest.MonkeyPatch) -> None:
    """보내기가 상한을 넘게 막히면 턴에 취소 신호를 보내고 자리를 돌려준다(쓰기 대기 상한, L1-08)."""
    monkeypatch.setattr(api, "WRITE_TIMEOUT_SECONDS", 0.05)
    entered, gate = threading.Event(), threading.Event()
    llm = _slow_llm(entered, gate)
    rt = _runtime(llm)

    async def main() -> str:
        resp = await anyio.to_thread.run_sync(
            lambda: api.handle_turn(
                json.dumps(_compare_request()).encode(), rt.deps(), secret=F.SECRET
            )
        )
        assert rt.try_acquire()
        stream = api.TurnStream(resp, rt.release, request_id="req-w")
        response = api.TurnStreamResponse(stream)

        async def send(message: Any) -> None:
            if message["type"] == "http.response.body" and message.get("body"):
                await anyio.sleep(1)  # 읽지 않는 소비자

        async def receive() -> Any:
            await anyio.sleep(10)
            return {"type": "http.disconnect"}

        scope = {"type": "http", "asgi": {"spec_version": "2.4"}}
        # 보내기 시간 초과는 OSError 계열이라 Starlette 가 연결 끊김(ClientDisconnect)으로 바꾼다
        with pytest.raises(Exception) as ei:  # noqa: B017  # 아래에서 종류를 본다
            await response(scope, receive, send)
        assert type(ei.value).__name__ in ("ClientDisconnect", "TimeoutError")
        return stream.reason

    reason = anyio.run(main)
    gate.set()
    assert reason == "write_timeout"
    assert _wait(lambda: rt.active == 0)
    assert "ANSWER" not in [c.purpose for c in llm.calls]


# ── 자료 묶음 (6-10 · L1-21 · L4-29) ─────────────────────────────────────────────


def test_service_reads_only_reviewed_cards_and_starts_without_them_now(
    log_records: list[logging.LogRecord],
) -> None:
    """지금 자료 카드는 검수 전이라 서비스는 자료 묶음 없이 시작한다(이유 코드만 로그, 앱은 뜬다)."""
    assert api.load_corpus() is None
    msgs = [r.getMessage() for r in log_records]
    lines = [m for m in msgs if m.startswith("chat_agent_corpus_unavailable reason=")]
    assert lines == ["chat_agent_corpus_unavailable reason=UNREVIEWED"]  # 이유 코드만, 카드 id 없음
    # 검수 여부만 빼면 묶음 자체(목록 · 해시 · 라이선스 · 형식)는 온전하다
    assert api.load_corpus(allow_unreviewed=True) is not None


def test_runtime_built_from_settings_has_no_corpus_until_cards_are_reviewed() -> None:
    rt = api.AgentRuntime.from_settings(_settings(agent_enabled=True, agent_state_secret=SECRET))
    assert rt.corpus is None and rt.corpus_version == "none"
