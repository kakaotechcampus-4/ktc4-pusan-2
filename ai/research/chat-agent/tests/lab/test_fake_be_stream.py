"""가짜 BE 문장 줄 시험 — 실행 이벤트 저장 · 커서 조회, 끝 줄 앞부분 대조, 끊겼을 때 받은 문장으로 PARTIAL (기능 문서 5-4 · 12-3, 실패 행 L2-44 · L2-45 · L2-46).

가짜 BE는 BE가 만들 실행 이벤트 · 스트림 검사의 기준 구현이다. 실제 코어(가짜 모델)의 줄을 받아, 정해 둔 방식으로 끊거나
바꾼 뒤 BE 처리를 본다.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from typing import Any

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
