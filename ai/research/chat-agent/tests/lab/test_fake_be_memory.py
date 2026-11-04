"""가짜 BE 기억 시험 — 기억 설정 · '기억 없이' 대화 · 기억 번호, 코칭 프로필 저장(다시 검사 · 번호 조건 · MEMORY_SAVED),
대화 요약 조건부 갱신 · 밀린 메시지 (기능 문서 6-14 · 12-3, 실패 행 L2-47 · L2-48 · L2-49 · L2-50).

가짜 BE는 BE가 만들 기억 저장의 기준 구현이다. 실제 코어(가짜 모델)로 돌린다.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from chat_agent_lab.fake_be import FakeBe, StoredMessage
from tests.unit import fakes as F

from .test_fake_be import make_be
from .test_fake_be_stream import TamperApp

#: 기록 주장이 없는 기억 요청(계획 단계의 판단 초안이 필요 없게)
MSG = "고마워. 앞으로는 설명을 짧게 해 줘"
UPD = {"key": "TONE", "value": "설명을 짧게", "quote": "앞으로는 설명을 짧게 해 줘"}


def mem_llm(updates: list[dict[str, str]] | None = None, *summaries: Any) -> F.FakeLlm:
    """계획 → 조사 끝 → 답(문장 없이 후보만) → (요약)."""
    return F.FakeLlm(
        [F.plan(), F.done(), *summaries],
        answers=[F.answer([], profile_updates=updates or [])],
        fixes=[F.fix({})],
    )


def last_request(be: FakeBe, run: Any) -> dict[str, Any]:
    return run.calls[-1].request


def stored(be: FakeBe, run: Any) -> StoredMessage:
    conv = be.conversations[run.conversation_id]
    return [m for m in conv.messages if m.run_id == run.run_id][-1]


# ── 기억 설정 · '기억 없이' · 번호 (L2-48 · L2-50) ─────────────────────────────


def test_memory_is_on_by_default_and_off_or_no_memory_sends_no_profile() -> None:
    be = make_be(mem_llm())
    run = be.send_message(be.new_conversation().conversation_id, "안녕")
    assert last_request(be, run)["memory"] == {"enabled": True, "memory_version": 0, "profile": []}

    be2 = make_be(mem_llm())
    run2 = be2.send_message(be2.new_conversation(no_memory=True).conversation_id, "안녕")
    assert last_request(be2, run2)["memory"] == {
        "enabled": False,
        "memory_version": 0,
        "profile": None,
    }

    be3 = make_be(mem_llm())
    be3.set_memory_enabled("u-3", False)
    run3 = be3.send_message(be3.new_conversation().conversation_id, "안녕")
    assert last_request(be3, run3)["memory"] == {
        "enabled": False,
        "memory_version": 1,
        "profile": None,
    }


def test_settings_change_and_deletes_bump_the_version_and_take_effect_next_turn() -> None:
    be = make_be(mem_llm([UPD]))
    conv = be.new_conversation()
    be.send_message(conv.conversation_id, MSG)
    (item,) = be.profile_items("u-3")
    v0 = be.memory_setting("u-3")["version"]
    be.delete_profile_item("u-3", item["item_id"])
    assert be.profile_items("u-3") == [] and be.memory_setting("u-3")["version"] == v0 + 1
    with pytest.raises(LookupError):
        be.delete_profile_item("u-3", item["item_id"])
    be.delete_all_profile("u-3")
    assert be.memory_setting("u-3")["version"] == v0 + 2
    be.app.llm = mem_llm()  # type: ignore[union-attr]
    run = be.send_message(conv.conversation_id, "안녕")
    assert last_request(be, run)["memory"]["profile"] == []


# ── 코칭 프로필 저장 (L2-47) ─────────────────────────────────────────────────


def test_explicit_request_is_saved_with_a_card_from_the_saved_value() -> None:
    be = make_be(mem_llm([UPD]))
    run = be.send_message(be.new_conversation().conversation_id, MSG)
    assert run.status in ("DONE", "PARTIAL")
    (item,) = be.profile_items("u-3")
    assert (item["key"], item["value"]) == ("TONE", "설명을 짧게")
    cards = [c for c in stored(be, run).cards if c["type"] == "MEMORY_SAVED"]
    assert cards == [{"type": "MEMORY_SAVED", **item}]
    # 다음 턴부터 프로필이 실린다(같은 key 는 하나, 새 값이 덮는다)
    be.app.llm = mem_llm(  # type: ignore[union-attr]
        [{"key": "TONE", "value": "쉬운 말로", "quote": "앞으로는 쉬운 말로 해 줘"}]
    )
    run2 = be.send_message(run.conversation_id, "앞으로는 쉬운 말로 해 줘")
    assert last_request(be, run2)["memory"]["profile"] == [item]
    assert [i["value"] for i in be.profile_items("u-3")] == ["쉬운 말로"]


def _inject(updates: list[dict[str, str]]) -> Any:
    def tamper(objs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        last = objs[-1]
        if last["event"] == "final" and last["data"]["status"] != "NEEDS_ACTION":
            last["data"]["profile_updates"] = updates
        return objs

    return tamper


def test_be_rechecks_candidates_and_refuses_what_ai_should_have_dropped() -> None:
    be = make_be(mem_llm())
    bad = [
        {"key": "TONE", "value": "30초 안에", "quote": "앞으로는 설명을 짧게 해 줘"},
        {"key": "CONTEXT", "value": "설명을 짧게", "quote": "남이 한 말"},
    ]
    be.app = TamperApp(be.app, _inject(bad))  # type: ignore[assignment]
    run = be.send_message(be.new_conversation().conversation_id, MSG)
    assert be.profile_items("u-3") == []
    assert not [c for c in stored(be, run).cards if c["type"] == "MEMORY_SAVED"]


def test_no_memory_conversation_or_off_setting_never_saves() -> None:
    be = make_be(mem_llm())
    be.app = TamperApp(be.app, _inject([UPD]))  # type: ignore[assignment]
    be.send_message(be.new_conversation(no_memory=True).conversation_id, MSG)
    assert be.profile_items("u-3") == []
    assert any(e.get("event") == "PROFILE_NOT_SAVED" for e in be.log)


def test_version_changed_during_the_run_blocks_saving() -> None:
    """실행을 시작한 뒤 기억 번호가 바뀌면(다른 탭에서 지움 · 끔) 그 실행의 후보는 저장하지 않는다(6-14 · L2-47)."""
    be = make_be(mem_llm([UPD]))
    inner = be.app

    class BumpThenCall:
        def turn(self, body: bytes, *, request_id: str | None = None) -> Any:
            be.delete_all_profile("u-3")  # 요청을 보낸 뒤(실행 중) 번호가 오른다
            return inner.turn(body, request_id=request_id)

    be.app = BumpThenCall()  # type: ignore[assignment]
    be.send_message(be.new_conversation().conversation_id, MSG)
    assert be.profile_items("u-3") == []
    assert any(e.get("event") == "PROFILE_NOT_SAVED" for e in be.log)


# ── 대화 요약 (L2-49) ──────────────────────────────────────────────────────


def _with_history(be: FakeBe, n: int) -> Any:
    conv = be.new_conversation()
    for i in range(n):
        conv.messages.append(
            StoredMessage(be._next("m"), "user" if i % 2 == 0 else "assistant", f"옛 말 {i}")
        )
    return conv


def test_overflow_is_sent_and_summary_replaces_only_on_same_version() -> None:
    be = make_be(mem_llm(None, F.summary(("TOPIC", "Take 5 의 3장 시선"))))
    conv = _with_history(be, 25)
    run = be.send_message(conv.conversation_id, "안녕")
    assert run.status != "FAILED"
    ov = last_request(be, run)["history"]["overflow"]
    assert [m["text"] for m in ov["messages"]] == [f"옛 말 {i}" for i in range(5)]
    assert ov["prev_summary_version"] == 0
    assert conv.summary == [{"kind": "TOPIC", "text": "Take 5 의 3장 시선"}]
    assert (conv.summary_version, conv.summary_covered) == (1, 5)

    # 다음 턴은 요약이 덮은 뒤로 밀린 메시지만, 새 판 번호로
    be.app.llm = mem_llm(None, F.summary())  # type: ignore[union-attr]
    run2 = be.send_message(conv.conversation_id, "다음")
    assert run2.status != "FAILED"
    ov2 = last_request(be, run2)["history"]["overflow"]
    assert ov2["prev_summary_version"] == 1
    assert [m["text"] for m in ov2["messages"]] == ["옛 말 5", "옛 말 6"]
    assert last_request(be, run2)["history"]["summary"] == [
        {"kind": "TOPIC", "text": "Take 5 의 3장 시선"}
    ]


def test_summary_version_conflict_or_null_keeps_the_old_summary() -> None:
    be = make_be(mem_llm(None, F.summary(("TOPIC", "새 요약"))))
    conv = _with_history(be, 22)
    inner = be.app

    class OtherWriterFirst:
        def turn(self, body: bytes, *, request_id: str | None = None) -> Any:
            conv.summary_version += 1  # 다른 실행이 먼저 요약을 바꿨다
            return inner.turn(body, request_id=request_id)

    be.app = OtherWriterFirst()  # type: ignore[assignment]
    be.send_message(conv.conversation_id, "안녕")
    assert conv.summary is None and conv.summary_covered == 0
    assert any(e.get("event") == "SUMMARY_VERSION_CONFLICT" for e in be.log)

    # 요약을 못 만들면(null) 바꾸지 않고 다음 턴에 같은 메시지를 다시 보낸다
    be2 = make_be(mem_llm(None, F.summary()))
    conv2 = _with_history(be2, 22)
    run = be2.send_message(conv2.conversation_id, "안녕")
    assert conv2.summary is None and conv2.summary_covered == 0
    first = last_request(be2, run)["history"]["overflow"]["messages"]
    be2.app.llm = mem_llm(None, F.summary())  # type: ignore[union-attr]
    run2 = be2.send_message(conv2.conversation_id, "다시")
    assert run2.status != "FAILED"
    assert last_request(be2, run2)["history"]["overflow"]["messages"][: len(first)] == first


def test_request_json_has_memory_and_overflow_shapes() -> None:
    be = make_be(mem_llm(None, F.summary()))
    run = be.send_message(_with_history(be, 21).conversation_id, "안녕")
    assert run.status != "FAILED"
    req = last_request(be, run)
    json.dumps(req)  # 직렬화 가능
    assert set(req["memory"]) == {"enabled", "memory_version", "profile"}
    assert set(req["history"]["overflow"]) == {"messages", "prev_summary_version"}
