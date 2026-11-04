"""가짜 BE 문장 줄 시험 — 실행 이벤트 저장 · 커서 조회, 끝 줄 앞부분 대조, 끊겼을 때 받은 문장으로 PARTIAL (기능 문서 5-4 · 12-3, 실패 행 L2-44 · L2-45 · L2-46).

가짜 BE는 BE가 만들 실행 이벤트 · 스트림 검사의 기준 구현이다. 실제 코어(가짜 모델)의 줄을 받아, 정해 둔 방식으로 끊거나
바꾼 뒤 BE 처리를 본다.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from typing import Any

import pytest

from chat_agent_lab.app import TurnOutcome
from chat_agent_lab.fake_be import FakeBe, Run
from tests.unit import fakes as F

from .test_fake_be import (
    GOAL_MSG,
    MSG,
    SAVE_ARGS,
    compare_with_fetch_script,
    make_be,
    request_draft,
)


class TamperApp:
    """진짜 서비스 계층 대역의 줄을 받아 `tamper`(줄 dict 목록 → 줄 dict 목록)로 바꿔 돌려준다."""

    def __init__(self, inner: Any, tamper: Callable[[list[dict[str, Any]]], list[dict[str, Any]]]):
        self.inner = inner
        self.tamper = tamper

    def turn(self, body: bytes, *, request_id: str | None = None) -> TurnOutcome:
        out = self.inner.turn(body, request_id=request_id)
        if out.lines is None:
            return out
        objs = self.tamper([json.loads(x) for x in out.lines])

        def gen() -> Iterator[str]:
            for o in objs:
                yield json.dumps(o, ensure_ascii=False, separators=(",", ":")) + "\n"

        return TurnOutcome(status=200, lines=gen())


def done_be(tamper: Callable[[list[dict[str, Any]]], list[dict[str, Any]]] | None = None) -> FakeBe:
    be = make_be(F.FakeLlm(compare_with_fetch_script()))
    if tamper is not None:
        be.app = TamperApp(be.app, tamper)  # type: ignore[assignment]
    return be


def claims_of(run: Run) -> list[dict[str, Any]]:
    return [e["data"]["claim"] for e in run.events if e["event"] == "claim"]


def stored(be: FakeBe, run: Run) -> Any:
    conv = be.conversations[run.conversation_id]
    return [m for m in conv.messages if m.run_id == run.run_id][-1]


def change_first_claim(final: dict[str, Any]) -> None:
    """끝 줄 message 의 첫 문장 글자를 바꾸고 `text`도 맞춘다(끝 줄 모양 검사는 통과하고 앞부분만 다르게)."""
    msg = final["message"]
    msg["claims"][0]["text"] += " 바뀜"
    msg["text"] = "\n".join(c["text"] for c in msg["claims"])


def save_goal_with_sentence() -> F.FakeLlm:
    """목표 저장(ASK) 앞 답에 판단 문장(코드 첫 문장)이 있는 대본: 멈추기 전에 문장 줄이 나간다."""
    measured = F.draft(
        "3장 시선 비율을 높이는 걸",
        claim_op="ISSUE",
        dimension="audience_gaze",
        metric="audience_ratio",
        slide_number=3,
        take_numbers=[5],
    )
    return F.FakeLlm(
        [
            F.plan(assessments=[measured, request_draft(GOAL_MSG)]),
            F.tools(("save_goal", SAVE_ARGS), prefix="sg"),
            F.done(),
        ],
        answers=[F.answer([F.claim("ASSESSMENT", "", assessment_index=0)]), F.answer([])],
        fixes=[F.fix({})],
    )


def only_last_call(fn: Callable[[list[dict[str, Any]]], list[dict[str, Any]]]) -> Any:
    """끝 줄이 DONE 인 응답(이어 부른 마지막 호출)만 바꾼다."""

    def tamper(objs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        last = objs[-1]
        if last["event"] == "final" and last["data"]["status"] == "DONE":
            return fn(objs)
        return objs

    return tamper


# ── 실행 이벤트 · 커서 (L2-46) ─────────────────────────────────────────────


def test_events_get_run_wide_seq_and_cursor_reads_only_newer_ones() -> None:
    be = done_be()
    run = be.send_message(None, MSG, page_take_id="t-5")
    assert run.status == "DONE"
    seqs = [e["event_seq"] for e in run.events]
    assert seqs == list(range(1, len(seqs) + 1))
    # 두 호출(행동 전 · 이어 부른 뒤)의 이벤트가 한 순번으로 이어진다
    assert {e["call"] for e in run.events} == {1, 2}
    # 끝 줄 message 는 받은 문장 줄과 같다
    assert claims_of(run) == run.final["message"]["claims"]  # type: ignore[index]
    mid = seqs[len(seqs) // 2]
    assert [e["event_seq"] for e in be.events_after(run.run_id, mid)] == seqs[mid:]
    assert be.events_after(run.run_id, seqs[-1]) == []
    assert be.events_after(run.run_id) == be.events_after(run.run_id, 0)


def test_claim_index_and_events_continue_across_the_approval_pause() -> None:
    be = make_be(save_goal_with_sentence())
    run = be.send_message(be.new_conversation().conversation_id, GOAL_MSG, page_take_id="t-5")
    assert run.status == "WAITING_APPROVAL"
    first = claims_of(run)
    assert first and first == run.final["message"]["claims"]  # type: ignore[index]
    pend = run.pending
    assert pend is not None
    out = be.approve(run.run_id, pend.action["action_id"], pend.args_hash)
    assert out.status == "DONE"
    idx = [e["data"]["index"] for e in out.events if e["event"] == "claim"]
    assert idx == list(range(len(idx)))
    assert claims_of(out)[: len(first)] == first
    seqs = [e["event_seq"] for e in out.events]
    assert seqs == list(range(1, len(seqs) + 1))


# ── 끊김 · error 끝 줄 · 앞부분 불일치 (L2-44 · L2-45) ───────────────────────────


def test_fm_L2_45_eof_after_claims_stores_received_claims_as_partial() -> None:
    be = done_be(only_last_call(lambda objs: objs[:-1]))  # 끝 줄 없이 끊김
    run = be.send_message(None, MSG, page_take_id="t-5")
    assert run.status == "PARTIAL"
    msg = stored(be, run)
    assert msg.status == "PARTIAL" and msg.claims == claims_of(run) and msg.claims
    assert msg.text == "\n".join(c["text"] for c in msg.claims)
    # 받은 판단 · 근거 사본도 함께 보존한다
    sent_as = {
        a["assessment_id"]
        for e in run.events
        if e["event"] == "claim"
        for a in e["data"]["assessments"]
    }
    assert {a["assessment_id"] for a in msg.assessments} == sent_as
    assert {e["evidence_id"] for e in msg.evidence} >= {
        i for c in msg.claims for i in c["evidence_ids"]
    }


def test_fm_L2_45_error_line_after_claims_is_partial_and_without_claims_is_upstream_error() -> None:
    def to_error(objs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        last = objs[-1]
        return [
            *objs[:-1],
            {
                **last,
                "event": "error",
                "data": {"error": {"code": "INTERNAL_ERROR", "message": "x"}},
            },
        ]

    be = done_be(only_last_call(to_error))
    run = be.send_message(None, MSG, page_take_id="t-5")
    assert run.status == "PARTIAL" and stored(be, run).claims == claims_of(run)

    # 문장 줄을 하나도 받지 못하고 끊기면 지금처럼 AGENT_UPSTREAM_ERROR
    be2 = done_be(
        only_last_call(lambda objs: [o for o in objs if o["event"] in ("plan", "tool_done")])
    )
    run2 = be2.send_message(None, MSG, page_take_id="t-5")
    assert (run2.status, run2.error_code) == ("FAILED", "AGENT_UPSTREAM_ERROR")


def test_fm_L2_44_final_prefix_mismatch_keeps_received_claims_and_runs_no_action() -> None:
    log: list[dict[str, Any]] = []

    def change_first(objs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        last = objs[-1]
        if last["event"] == "final" and last["data"]["message"]:
            change_first_claim(last["data"])
        return objs

    llm = F.FakeLlm(compare_with_fetch_script())
    be = make_be(llm)
    be.log = log
    be.app = TamperApp(be.app, only_last_call(change_first))  # type: ignore[assignment]
    run = be.send_message(None, MSG, page_take_id="t-5")
    assert run.status == "PARTIAL"
    assert any(e.get("event") == "CLAIM_PREFIX_MISMATCH" for e in log)
    assert stored(be, run).claims == claims_of(run)
    assert all("바뀜" not in c["text"] for c in stored(be, run).claims)


def test_claim_index_gap_or_repeat_is_rejected_and_received_claims_are_kept() -> None:
    def gap(objs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        cl = [o for o in objs if o["event"] == "claim"]
        cl[1]["data"]["index"] += 1  # 둘째 문장 줄 번호를 건너뜀
        return objs

    be = done_be(only_last_call(gap))
    run = be.send_message(None, MSG, page_take_id="t-5")
    assert run.status == "PARTIAL"
    assert run.calls[-1].error_code == "CLAIM_INVALID"
    assert len(claims_of(run)) == 1 and stored(be, run).claims == claims_of(run)


def test_needs_action_with_tampered_prefix_does_not_execute_the_action() -> None:
    """승인 대기 전 응답(NEEDS_ACTION)도 앞부분이 다르면 행동을 받지 않는다(카드 · 대기 없음)."""

    def change(objs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        last = objs[-1]
        if last["event"] == "final" and last["data"]["status"] == "NEEDS_ACTION":
            change_first_claim(last["data"])
        return objs

    be = make_be(save_goal_with_sentence())
    be.app = TamperApp(be.app, change)  # type: ignore[assignment]
    run = be.send_message(be.new_conversation().conversation_id, GOAL_MSG, page_take_id="t-5")
    assert run.status == "PARTIAL" and run.pending is None and run.actions == []
    assert stored(be, run).claims == claims_of(run)


# ── 받는 즉시 저장 · 전송 오류 · 스트림 전 오류 · 그 밖의 실패 끝 ───────────────────


class StreamApp:
    """진짜 서비스 계층 대역의 줄을 하나씩 넘기는 대역. `on_line(줄 dict)`을 줄을 넘긴 뒤(받는 쪽이 처리한 뒤) 부른다.
    `cut_after_claims`개의 문장 줄을 넘긴 뒤(0 이면 첫 줄 전에) `cut_with` 예외를 낸다. `delay`초 뒤에 응답을 돌려주고
    `reformat`이면 줄을 공백 · 다른 키 순서로 다시 쓴다(유효한 JSON). 끝나거나 닫히면 안쪽 응답도 닫는다(자리 반환)."""

    def __init__(
        self,
        inner: Any,
        *,
        on_line: Callable[[dict[str, Any]], None] | None = None,
        cut_after_claims: int | None = None,
        cut_with: type[Exception] = ConnectionError,
        delay: float = 0.0,
        reformat: bool = False,
    ) -> None:
        self.inner = inner
        self.on_line = on_line
        self.cut_after_claims = cut_after_claims
        self.cut_with = cut_with
        self.delay = delay
        self.reformat = reformat

    def turn(self, body: bytes, *, request_id: str | None = None) -> TurnOutcome:
        import time

        time.sleep(self.delay)
        out = self.inner.turn(body, request_id=request_id)
        if out.lines is None:
            return out
        src = out.lines

        def gen() -> Iterator[str]:
            seen = 0
            try:
                if self.cut_after_claims == 0:
                    raise self.cut_with("끊김")
                for raw in src:
                    obj = json.loads(raw)
                    if self.reformat:
                        obj = {
                            "data": obj["data"],
                            "seq": obj["seq"],
                            "run_id": obj["run_id"],
                            "event": obj["event"],
                        }
                        yield json.dumps(obj, ensure_ascii=False) + "\n"
                    else:
                        yield raw
                    if self.on_line is not None:
                        self.on_line(obj)
                    if obj["event"] == "claim":
                        seen += 1
                        if self.cut_after_claims is not None and seen >= self.cut_after_claims:
                            raise self.cut_with("끊김")
            finally:
                close = getattr(src, "close", None)
                if callable(close):
                    close()

        return TurnOutcome(status=200, lines=gen())


def one_slot(be: FakeBe) -> Any:
    """서비스 계층 대역의 동시 실행 자리를 1개로 줄인다(자리를 돌려줬는지 정확히 보려고)."""
    import threading

    be.app._slots = threading.BoundedSemaphore(1)  # type: ignore[attr-defined]
    return be.app


def test_lines_are_stored_as_they_arrive_and_cursor_sees_them_before_the_end() -> None:
    be = done_be()
    seen: list[tuple[int, int]] = []

    def on_line(obj: dict[str, Any]) -> None:
        if obj["event"] == "claim":
            run = next(iter(be.runs.values()))
            # 받는 쪽이 이 줄을 처리한 뒤: 끝 줄 전인데도 커서 조회에 이 문장이 보인다
            got = [e for e in be.events_after(run.run_id) if e["event"] == "claim"]
            seen.append((obj["data"]["index"], got[-1]["data"]["index"]))

    be.app = StreamApp(be.app, on_line=on_line)  # type: ignore[assignment]
    run = be.send_message(None, MSG, page_take_id="t-5")
    assert run.status == "DONE" and seen and all(a == b for a, b in seen)


def test_connection_error_after_claims_keeps_them_and_returns_the_slot() -> None:
    be = done_be()
    inner = one_slot(be)
    be.app = StreamApp(inner, cut_after_claims=1)  # type: ignore[assignment]
    run = be.send_message(None, MSG, page_take_id="t-5")
    assert run.status == "PARTIAL" and run.calls[-1].error_code == "STREAM_CUT"
    assert len(claims_of(run)) == 1 and stored(be, run).claims == claims_of(run)
    assert inner._slots.acquire(blocking=False)  # 자리 하나를 돌려줬다
    inner._slots.release()


@pytest.mark.parametrize(
    ("cut_with", "code"),
    [(ConnectionError, "AGENT_UPSTREAM_ERROR"), (TimeoutError, "AGENT_TIMEOUT")],
)
def test_transport_error_before_any_claim_fails_with_the_mapped_code(
    cut_with: type[Exception], code: str
) -> None:
    """문장 줄을 받기 전 끊기면 PARTIAL 이 아니라 실패다: 시간 초과는 AGENT_TIMEOUT, 그 밖은 AGENT_UPSTREAM_ERROR(L2-15)."""
    be = done_be()
    inner = one_slot(be)

    def cut_last_call(body: bytes, *, request_id: str | None = None) -> TurnOutcome:
        # 처음 호출(행동 전, 문장 없음)은 그대로, 이어 부른 호출은 첫 줄 전에 끊는다
        app = StreamApp(
            inner, cut_after_claims=0 if b"ACTION_RESULT" in body else None, cut_with=cut_with
        )
        return app.turn(body, request_id=request_id)

    be.app = type("A", (), {"turn": staticmethod(cut_last_call)})()  # type: ignore[assignment]
    run = be.send_message(None, MSG, page_take_id="t-5")
    assert (run.status, run.error_code) == ("FAILED", code)
    assert claims_of(run) == []
    assert inner._slots.acquire(blocking=False)
    inner._slots.release()


def test_unexpected_iterator_error_is_not_hidden_as_a_cut() -> None:
    """전송 오류가 아닌 예외(버그)는 부분 실패로 숨기지 않고 올린다."""
    be = done_be()
    be.app = StreamApp(be.app, cut_after_claims=1, cut_with=ValueError)  # type: ignore[assignment]
    with pytest.raises(ValueError):
        be.send_message(None, MSG, page_take_id="t-5")


def test_pre_stream_error_on_resume_fails_with_the_passed_code() -> None:
    """승인 뒤 이어 부르기의 스트림 전 오류는 받은 문장이 있어도 실행 오류 코드 규칙(5-9)대로 FAILED 다(L1-41 · L2-15)."""
    be = make_be(save_goal_with_sentence())
    run = be.send_message(be.new_conversation().conversation_id, GOAL_MSG, page_take_id="t-5")
    pend = run.pending
    assert claims_of(run) and pend is not None
    be.app = ReplayError(409, "AGENT_STATE_INVALID")  # type: ignore[assignment]
    out = be.approve(run.run_id, pend.action["action_id"], pend.args_hash)
    assert (out.status, out.error_code) == ("FAILED", "AGENT_STATE_INVALID")


class ReplayError:
    def __init__(self, status: int, code: str) -> None:
        self.status, self.code = status, code

    def turn(self, body: bytes, *, request_id: str | None = None) -> TurnOutcome:
        return TurnOutcome(status=self.status, error={"error": {"code": self.code, "message": "x"}})


def test_invalid_final_after_claims_keeps_them() -> None:
    def bad_text(objs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        objs[-1]["data"]["message"]["text"] = (
            "바뀐 글"  # text 가 문장 이음과 달라 끝 줄 검사에 걸린다
        )
        return objs

    be = done_be(only_last_call(bad_text))
    run = be.send_message(None, MSG, page_take_id="t-5")
    assert run.status == "PARTIAL" and run.calls[-1].error_code == "FINAL_INVALID"
    assert stored(be, run).claims == claims_of(run) and claims_of(run)


def test_call_limit_after_claims_keeps_them_without_running_the_action() -> None:
    """승인 뒤 이어 부른 응답이 다시 읽기 행동을 낼 때 호출 상한에 닿으면 받은 문장으로 PARTIAL(행동 실행 안 함)."""
    from .test_fake_be import fetch

    llm = save_goal_with_sentence()
    llm.script[-1] = fetch("t-4", "ft")  # 이어 부른 뒤 조사: 조사 끝 대신 읽기 행동
    be = make_be(llm, max_calls=2)
    run = be.send_message(be.new_conversation().conversation_id, GOAL_MSG, page_take_id="t-5")
    pend = run.pending
    assert pend is not None
    out = be.approve(run.run_id, pend.action["action_id"], pend.args_hash)
    assert out.status == "PARTIAL" and len(out.calls) == 2
    assert [a.action["type"] for a in out.actions] == ["SAVE_GOAL"]  # FETCH_TAKE 는 실행하지 않음
    assert stored(be, out).claims == claims_of(out)


def test_claim_index_repeat_or_wrong_first_index_is_rejected() -> None:
    def repeat(objs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        cl = [o for o in objs if o["event"] == "claim"]
        cl[1]["data"]["index"] = cl[0]["data"]["index"]
        return objs

    def wrong_first(objs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        next(o for o in objs if o["event"] == "claim")["data"]["index"] += 1
        return objs

    run = done_be(only_last_call(repeat)).send_message(None, MSG, page_take_id="t-5")
    assert run.status == "PARTIAL" and len(claims_of(run)) == 1
    be2 = done_be(only_last_call(wrong_first))
    run2 = be2.send_message(None, MSG, page_take_id="t-5")
    assert (run2.status, run2.calls[-1].error_code) == ("FAILED", "CLAIM_INVALID")
    assert claims_of(run2) == []


def test_first_claim_time_counts_from_before_the_call_and_reads_parsed_lines() -> None:
    be = done_be()
    be.app = StreamApp(be.app, delay=0.03, reformat=True)  # type: ignore[assignment]
    run = be.send_message(None, MSG, page_take_id="t-5")
    assert run.status == "DONE"
    last = run.calls[-1]
    assert last.first_claim_ms is not None and last.first_claim_ms >= 30
    assert last.end_ms is not None and last.end_ms >= last.first_claim_ms
