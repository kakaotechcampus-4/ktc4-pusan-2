"""서비스 계층 대역(chat_agent_lab/app.py) 시험 — 동시 실행 자리 · 끄기 · 스트림 전 오류 (기능 문서 5-4 · 8-2 · 11-1)."""

from __future__ import annotations

import json

from chat_agent_lab.app import AgentApp
from tests.unit import fakes as F


def app(**kw: object) -> AgentApp:
    return AgentApp(
        llm=F.FakeLlm([F.plan(route="OUT_OF_SCOPE", steps=[])] * 8),
        secret=F.SECRET,
        corpus=None,
        **kw,
    )  # type: ignore[arg-type]


def body() -> bytes:
    return json.dumps(F.request(user_message="오늘 날씨 어때?"), ensure_ascii=False).encode("utf-8")


def test_busy_when_no_slot_and_slot_returned_after_close_without_reading() -> None:
    """자리가 없으면 스트림 전 429 AGENT_BUSY. 한 줄도 읽지 않고 닫아도 자리를 돌려준다(L1-08)."""
    a = app(max_concurrency=1)
    first = a.turn(body())
    assert first.status == 200 and first.lines is not None
    busy = a.turn(body())
    assert (busy.status, busy.error["error"]["code"]) == (429, "AGENT_BUSY")  # type: ignore[index]
    first.lines.close()  # type: ignore[attr-defined]
    again = a.turn(body())
    assert again.status == 200 and again.lines is not None
    lines = [json.loads(x) for x in again.lines]
    assert lines[-1]["data"]["status"] == "REFUSED"
    assert a.turn(body()).status == 200  # 끝까지 읽은 뒤에도 자리가 돌아왔다


def test_disabled_and_pre_stream_error_release_slot() -> None:
    off = app(enabled=False)
    assert off.turn(body()).error["error"]["code"] == "AGENT_DISABLED"  # type: ignore[index]
    a = app(max_concurrency=1)
    bad = a.turn(b"{")
    assert bad.status == 422
    assert a.turn(body()).status == 200


def test_fm_L1_14_busy_when_model_pool_is_saturated() -> None:
    a = app()
    a.llm.saturated = lambda: True  # type: ignore[union-attr]
    out = a.turn(body())
    assert (out.status, out.error["error"]["code"]) == (429, "AGENT_BUSY")  # type: ignore[index]
