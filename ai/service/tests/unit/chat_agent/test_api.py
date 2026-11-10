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
    """켜짐 + 서명 키. 시험이 `app.state.chat_agent_runtime` 에 가짜 모델 자원을 넣는다."""
    with TestClient(create_app(_settings(agent_enabled=True, agent_state_secret=SECRET))) as c:
        yield c


@pytest.fixture
def keyless_client() -> Iterator[TestClient]:
    """켜졌지만 서명 키가 없다."""
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
    keyless_client: TestClient,
) -> None:
    """대화를 켜도(서명 키 · 모델 없음) 기존 리뷰는 기본 결과 200 그대로다(L5-01 · L5-33)."""
    from pitch_coach_ai.features.review import core as review_core

    req = json.loads(
        (Path(review_core.__file__).parent / "examples" / "review" / "request.json").read_text(
            encoding="utf-8"
        )
    )
    assert keyless_client.post(URL, json=_compare_request()).status_code == 503
    r = keyless_client.post("/takes/review", json=req)
    assert r.status_code == 200 and r.json()["fallback_reason"] == "LLM_UNAVAILABLE"


def test_enabled_without_signing_key_is_503_agent_disabled_only_on_the_chat_path(
    keyless_client: TestClient,
) -> None:
    r = keyless_client.post(URL, json=_compare_request())
    assert (r.status_code, r.json()["error"]["code"]) == (503, "AGENT_DISABLED")
    assert keyless_client.get("/health").status_code == 200


@pytest.mark.parametrize("secret", [None, SecretStr("short")])
@pytest.mark.parametrize(
    "body", [b"{not json", b"x" * (2 * 1024 * 1024 + 1)], ids=["bad-json", "over-2mib"]
)
def test_missing_or_short_key_is_503_before_building_or_reading_anything(
    secret: SecretStr | None, body: bytes
) -> None:
    """키가 없거나 짧으면 본문이 잘못됐거나 커도 503 `AGENT_DISABLED` 이고 자원을 만들지 않는다(11-1)."""
    with TestClient(create_app(_settings(agent_enabled=True, agent_state_secret=secret))) as c:
        r = c.post(URL, content=body, headers={"content-type": "application/json"})
        assert (r.status_code, r.json()["error"]["code"]) == (503, "AGENT_DISABLED")
        assert getattr(c.app.state, "chat_agent_runtime", None) is None


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
    app = create_app(_settings(agent_enabled=True, agent_state_secret=SECRET))
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
    app = create_app(_settings(agent_enabled=True, agent_state_secret=SECRET))
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
    app = create_app(_settings(agent_enabled=True, agent_state_secret=SECRET))
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


def test_send_blocked_over_two_seconds_cancels_the_turn(
    monkeypatch: pytest.MonkeyPatch, log_records: list[logging.LogRecord]
) -> None:
    """보내기가 상한을 넘게 막히면 턴에 취소 신호를 보내고 자리를 돌려준다(쓰기 대기 상한, L1-08).

    느린 소비자는 계약이 예상한 상황이라 예외로 올리지 않고 한 줄만 남긴다."""
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
        await response(scope, receive, send)  # 예외로 올리지 않는다
        return stream.reason

    reason = anyio.run(main)
    gate.set()
    assert reason == "write_timeout"
    assert _wait(lambda: rt.active == 0)
    assert "ANSWER" not in [c.purpose for c in llm.calls]
    msgs = [r.getMessage() for r in log_records]
    assert [m for m in msgs if m.startswith("agent_stream_write_timeout")] == [
        "agent_stream_write_timeout request_id=req-w lines=0"
    ]
    assert [m.split(" duration_ms")[0] for m in msgs if m.startswith("agent_stream ")] == [
        "agent_stream request_id=req-w end=write_timeout lines=0"
    ]


def test_send_blocked_through_the_whole_app_leaves_no_unhandled_error(
    monkeypatch: pytest.MonkeyPatch, log_records: list[logging.LogRecord]
) -> None:
    """운영 구성(ASGI 2.3 + 요청 로그 미들웨어)으로 앱 전체를 부르고 서버 쪽 보내기를 계속 막는다.

    - 2초 상한은 서버로 실제로 보내는 곳에서 잰다(미들웨어 내부 큐가 아니라): 막힌 보내기를 끊고
      앱이 돌아온다(서버가 연결을 닫는다). 예외 · 처리되지 않은 오류 로그로 올라가지 않는다.
    - 요약 줄의 `lines=` 는 서버로 실제로 보낸 줄 수다. 받는 쪽은 끝 줄 없는 응답으로 본다(5-4)."""
    monkeypatch.setattr(api, "WRITE_TIMEOUT_SECONDS", 0.05)
    rt = _runtime(_compare_llm())
    app = create_app(_settings(agent_enabled=True, agent_state_secret=SECRET))
    app.state.chat_agent_runtime = rt
    body = json.dumps(_compare_request()).encode()
    sent: list[bytes] = []

    async def main() -> None:
        state = {"read": False}

        async def receive() -> Any:
            if not state["read"]:
                state["read"] = True
                return {"type": "http.request", "body": body, "more_body": False}
            await anyio.sleep(10)
            return {"type": "http.disconnect"}

        async def send(message: Any) -> None:
            if message["type"] == "http.response.body" and message.get("body"):
                if sent:
                    await anyio.sleep_forever()  # 첫 줄 뒤로는 읽지 않는 소비자(서버 버퍼가 참)
                sent.append(message["body"])

        with anyio.fail_after(5):
            await app(_asgi_scope(body, spec="2.3"), receive, send)  # 예외 없이 끝난다

    anyio.run(main)
    assert _wait(lambda: rt.active == 0)
    msgs = [r.getMessage() for r in log_records]
    assert not any(m.startswith("unhandled_error") for m in msgs)
    assert not any(r.levelno >= logging.ERROR for r in log_records)
    assert [m for m in msgs if m.startswith("agent_stream_write_timeout")] == [
        "agent_stream_write_timeout request_id=" + _rid(msgs) + " lines=1"
    ]
    ends = [m for m in msgs if m.startswith("agent_stream ")]
    assert len(ends) == 1 and " end=write_timeout lines=1 " in ends[0]  # 실제로 보낸 줄 수
    got = [json.loads(x) for x in b"".join(sent).splitlines() if x]
    assert [ln["event"] for ln in got] == ["plan"]
    assert not any(ln["event"] in ("final", "error") for ln in got)  # 끝 줄 없이 끝남


def test_send_blocked_during_a_model_call_ends_the_response_without_waiting_for_the_model(
    monkeypatch: pytest.MonkeyPatch, log_records: list[logging.LogRecord]
) -> None:
    """앱 전체(ASGI 2.3)에서 첫 줄 뒤 서버 보내기가 막히고 모델 호출은 아직 도는 중이어도, 상한이 지나면
    모델을 기다리지 않고 앱이 돌아온다(연결 정리). 자리는 코어가 끝난 뒤 돌려준다."""
    monkeypatch.setattr(api, "WRITE_TIMEOUT_SECONDS", 0.05)
    entered, gate = threading.Event(), threading.Event()
    llm = _slow_llm(entered, gate)
    rt = _runtime(llm)
    app = create_app(_settings(agent_enabled=True, agent_state_secret=SECRET))
    app.state.chat_agent_runtime = rt
    body = json.dumps(_compare_request()).encode()

    async def main() -> float:
        state = {"read": False}

        async def receive() -> Any:
            if not state["read"]:
                state["read"] = True
                return {"type": "http.request", "body": body, "more_body": False}
            await anyio.sleep(10)
            return {"type": "http.disconnect"}

        async def send(message: Any) -> None:
            if message["type"] == "http.response.body" and message.get("body"):
                # 모델이 조사 호출 안에서 기다리는 동안 첫 줄 보내기가 막힌다(서버 버퍼가 참)
                await anyio.to_thread.run_sync(entered.wait, 3)
                await anyio.sleep_forever()

        started = time.monotonic()
        with anyio.fail_after(5):
            await app(_asgi_scope(body, spec="2.3"), receive, send)
        return time.monotonic() - started

    took = anyio.run(main)
    assert not gate.is_set()  # 모델 호출이 아직 막혀 있는 동안 돌아왔다
    assert took < 2.5
    gate.set()
    assert _wait(lambda: rt.active == 0)
    assert "ANSWER" not in [c.purpose for c in llm.calls]
    msgs = [r.getMessage() for r in log_records]
    ends = [m for m in msgs if m.startswith("agent_stream ")]
    assert len(ends) == 1 and " end=write_timeout " in ends[0]


def test_send_guard_is_the_outermost_user_middleware() -> None:
    """보내기 상한 미들웨어는 요청 로그(`BaseHTTPMiddleware`)보다 바깥이어야 실제 보내기를 잰다(안쪽이면
    조용히 내부 큐까지만 잰다). 미들웨어를 더하거나 순서를 바꿔도 이 순서가 깨지지 않게 고정한다."""
    app = create_app(_settings())
    assert app.user_middleware[0].cls is api.StreamSendGuard


@pytest.mark.parametrize(
    ("root_path", "path", "guarded"),
    [
        ("", "/agent/turn", True),
        ("/ai", "/ai/agent/turn", True),  # 경로 접두어를 붙여 배포해도 건다(라우터와 같은 기준)
        ("", "/agent/turns", False),
        ("", "/health", False),
        ("/ai", "/aiagent/turn", False),
    ],
)
def test_send_guard_matches_the_route_path_like_the_router(
    root_path: str, path: str, guarded: bool
) -> None:
    seen: list[bool] = []

    async def inner(scope: Any, _receive: Any, _send: Any) -> None:
        seen.append(api.SEND_METER in scope)

    async def noop(*_a: Any) -> Any:
        return None

    scope = {"type": "http", "path": path, "root_path": root_path}
    anyio.run(api.StreamSendGuard(inner), scope, noop, noop)
    assert seen == [guarded]


def test_core_lines_start_with_the_event_key_for_end_line_detection() -> None:
    """읽기 스레드는 줄을 다시 해석하지 않고 앞부분(`{"event":"final"` · `{"event":"error"`)으로 끝 줄을
    알고, seq 는 줄 수로 센다. 코어 줄 쓰기가 이 모양을 지키는지 고정한다."""
    rt = _runtime(_compare_llm())
    resp = api.handle_turn(json.dumps(_compare_request()).encode(), rt.deps(), secret=F.SECRET)
    assert resp.lines is not None and resp.run_id == "run-81"
    lines = list(resp.lines)
    for i, line in enumerate(lines, start=1):
        obj = json.loads(line)
        assert line.startswith('{"event":"' + obj["event"] + '"') and obj["seq"] == i
    assert lines[-1].startswith(api._TERMINAL_PREFIXES)
    assert not any(x.startswith(api._TERMINAL_PREFIXES) for x in lines[:-1])


def _rid(msgs: list[str]) -> str:
    """요청 로그 줄에서 요청 ID(미들웨어가 만든 값)를 꺼낸다."""
    line = next(m for m in msgs if m.startswith("request_id=") and "path=/agent/turn" in m)
    return line.split()[0].removeprefix("request_id=")


def test_normal_end_is_never_logged_as_client_gone() -> None:
    """코어 줄을 모두 보낸 정상 끝은 늘 `done` 이다. 읽기 스레드가 끝 표시를 넣은 직후 아직 돌아오지
    않았어도(스레드가 살아 있음) 끊김으로 세지 않는다(정상 끝 경쟁 조건)."""

    def fast_lines() -> Iterator[str]:
        yield '{"event":"final","run_id":"run-f","seq":1,"data":{}}\n'

    async def one() -> str:
        resp = api.TurnResponse(lines=fast_lines(), cancel=lambda: None, run_id="run-f")
        stream = api.TurnStream(resp, lambda: None, request_id="req-f")

        async def send(_m: Any) -> None:
            return None

        async def receive() -> Any:
            await anyio.sleep(10)
            return {"type": "http.disconnect"}

        await api.TurnStreamResponse(stream)(
            {"type": "http", "asgi": {"spec_version": "2.4"}}, receive, send
        )
        return stream.reason

    async def many() -> list[str]:
        return [await one() for _ in range(300)]

    assert set(anyio.run(many)) == {"done"}


def test_disconnect_while_sending_the_end_line_shows_in_the_summary(
    log_records: list[logging.LogRecord],
) -> None:
    """코어가 끝난 뒤 끝 줄을 보내다 끊겨도 요약 줄의 `end=` 에 드러난다(펌프가 다 읽은 때가 아니라
    응답이 끝난 때 남긴다)."""
    rt = _runtime(_compare_llm())

    async def main() -> None:
        resp = await anyio.to_thread.run_sync(
            lambda: api.handle_turn(
                json.dumps(_compare_request()).encode(), rt.deps(), secret=F.SECRET
            )
        )
        assert rt.try_acquire()
        stream = api.TurnStream(resp, rt.release, request_id="req-end")
        response = api.TurnStreamResponse(stream)

        async def send(message: Any) -> None:
            if b'"event":"final"' in message.get("body", b""):
                raise OSError("끊김")

        async def receive() -> Any:
            await anyio.sleep(10)
            return {"type": "http.disconnect"}

        with pytest.raises(Exception):  # noqa: B017  # 2.4 는 Starlette 가 ClientDisconnect 로 바꾼다
            await response({"type": "http", "asgi": {"spec_version": "2.4"}}, receive, send)

    anyio.run(main)
    assert _wait(lambda: rt.active == 0)
    ends = [r.getMessage() for r in log_records if r.getMessage().startswith("agent_stream ")]
    assert len(ends) == 1 and " end=client_gone " in ends[0]


def test_core_iterator_exception_still_sends_one_error_end_line(
    log_records: list[logging.LogRecord],
) -> None:
    """코어 반복자가 끝 줄 없이 예외로 멈추면 읽기 스레드가 `error` 끝 줄 하나를 대신 낸다(두 번째 방어,
    5-4 '끝 줄은 정확히 하나' · L1-08). 자리는 돌려준다."""
    plan = json.dumps(
        {"event": "plan", "run_id": "run-x", "seq": 1, "data": {}}, separators=(",", ":")
    )

    def lines() -> Iterator[str]:
        yield plan + "\n"
        raise RuntimeError("코어 버그")

    released: list[int] = []
    sent: list[bytes] = []

    async def main() -> str:
        resp = api.TurnResponse(lines=lines(), cancel=lambda: None, run_id="run-x")
        stream = api.TurnStream(resp, lambda: released.append(1), request_id="req-p")
        response = api.TurnStreamResponse(stream)

        async def send(message: Any) -> None:
            if message.get("body"):
                sent.append(message["body"])

        async def receive() -> Any:
            await anyio.sleep(10)
            return {"type": "http.disconnect"}

        await response({"type": "http", "asgi": {"spec_version": "2.4"}}, receive, send)
        return stream.reason

    assert anyio.run(main) == "pump_error"
    got = [json.loads(x) for x in b"".join(sent).splitlines() if x]
    assert [ln["event"] for ln in got] == ["plan", "error"]
    assert got[1]["run_id"] == "run-x" and got[1]["seq"] == 2
    assert got[1]["data"]["error"]["code"] == "INTERNAL_ERROR"
    assert released == [1]
    msgs = [r.getMessage() for r in log_records]
    assert "agent_stream_error request_id=req-p cause=RuntimeError" in msgs
    assert not any("코어 버그" in m for m in msgs)


# ── 자료 묶음 (6-10 · L1-21 · L4-29) ─────────────────────────────────────────────


def test_service_reads_only_reviewed_cards(log_records: list[logging.LogRecord]) -> None:
    """서비스는 검수를 마친 카드만 싣는다(카드 단위 검수, P-0008 5-2). 발표법 카드(g-19 는 참고 문헌이 없어 검수 전)와, guides-9 부터
    FE 검수(#1129)를 반영한 사용법 카드 25장이 실린다."""
    served = api.load_corpus()
    assert served is not None and served.version == "guides-9"
    assert {c.kind for c in served.cards} == {"method", "usage"}
    assert sum(c.kind == "usage" for c in served.cards) == 25
    assert all(c.review == "APPROVED" for c in served.cards)
    assert not [
        r for r in log_records if r.getMessage().startswith("chat_agent_corpus_unavailable")
    ]
    every = api.load_corpus(allow_unreviewed=True)
    assert every is not None and len(every.cards) > len(served.cards)


def test_service_starts_without_corpus_when_no_card_is_reviewed(
    monkeypatch: pytest.MonkeyPatch, log_records: list[logging.LogRecord]
) -> None:
    """검수를 마친 카드가 하나도 없으면 자료 묶음 없이 시작한다(이유 코드만 로그, 앱은 뜬다)."""
    from pitch_coach_ai.features.chat_agent import corpus as corpus_mod

    real = corpus_mod.build_corpus

    def none_approved(manifest: Any, files: Any, **kw: Any) -> Any:
        files = {n: [{**c, "review": "PENDING"} for c in cards] for n, cards in files.items()}
        raw = [c for n in manifest["files"] for c in files[n]]
        manifest = {
            **manifest,
            "sha256": corpus_mod.manifest_hash(raw, manifest.get("synonyms") or None),
        }
        monkeypatch.setitem(
            corpus_mod.CORPUS_VERSION_HASHES, manifest["corpus_version"], manifest["sha256"]
        )
        return real(manifest, files, **kw)

    monkeypatch.setattr(api, "build_corpus", none_approved)
    assert api.load_corpus() is None
    msgs = [r.getMessage() for r in log_records]
    lines = [m for m in msgs if m.startswith("chat_agent_corpus_unavailable reason=")]
    assert lines == ["chat_agent_corpus_unavailable reason=UNREVIEWED"]  # 이유 코드만, 카드 id 없음


def test_runtime_is_built_once_per_app_even_under_concurrent_first_requests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`tool_llm` 은 부를 때마다 새 실행 풀을 만들므로, 자원은 앱(프로세스)에서 한 번만 만든다."""
    built: list[dict[str, Any]] = []

    def fake_tool_llm(*_a: Any, **kw: Any) -> Any:
        built.append(kw)
        time.sleep(0.05)  # 만드는 동안 다른 요청이 들어온다
        return F.FakeLlm([])

    monkeypatch.setattr(api, "tool_llm", fake_tool_llm)
    app = create_app(_settings(agent_enabled=True, agent_state_secret=SECRET))
    got: list[Any] = []
    threads = [threading.Thread(target=lambda: got.append(api.runtime_for(app))) for _ in range(4)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    got.append(api.runtime_for(app))
    assert len(built) == 1
    assert all(rt is got[0] and rt.llm is got[0].llm for rt in got)


def test_runtime_caps_one_model_call_at_the_core_answer_share(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """대화 호출 한 번의 상한은 코어 설정의 답 몫(24초 / 2 = 12초)이다(`LLM_TIMEOUT_SECONDS` 를 올려도)."""
    seen: list[dict[str, Any]] = []
    monkeypatch.setattr(api, "tool_llm", lambda *_a, **kw: seen.append(kw) or None)
    s = _settings(agent_enabled=True, agent_state_secret=SECRET, llm_timeout_seconds=20)
    api.AgentRuntime.from_settings(s)
    assert seen[0]["max_call_seconds"] == 12.0 and seen[0]["pool_size"] == 8


def test_runtime_built_from_settings_serves_the_reviewed_cards() -> None:
    rt = api.AgentRuntime.from_settings(_settings(agent_enabled=True, agent_state_secret=SECRET))
    assert rt.corpus is not None and rt.corpus_version == "guides-9"
