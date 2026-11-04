"""가짜 BE 승인 흐름 시험 — 승인 대기 · 승인 · 거절 · 만료 · 대기 중 새 메시지 · 해시 불일치 · 승인과 취소 경합 · 선승인 해시 재계산 (기능 문서 6-2 · 12-3).

실제 모델 없이 정해 둔 응답(tests/unit/fakes.py)을 쓰고, BE 쪽은 chat_agent_lab/fake_be.py 가 맡는다.
이름의 L 번호는 기능 문서 7절 실패 행이다(README '실패 행 대응표').
"""

from __future__ import annotations

from typing import Any

import pytest

from chat_agent import templates
from chat_agent.actions import action_args_hash
from chat_agent_lab.fake_be import (
    APPROVAL_WAIT_MS,
    ApprovalError,
    FakeBe,
    Run,
    content_hash,
)
from tests.unit import fakes as F

from .test_fake_be import (
    GOAL_MSG,
    SAVE_ARGS,
    SCRIPT_MSG,
    cards,
    make_be,
    official_reviews,
    ok,
    reassess_llm,
    request_draft,
    save_goal_llm,
    script_drafts,
)

REJECT_TEXT = templates.SystemText.REJECTED_SAVE_GOAL.value
FIRST = F.answer([F.claim("ASSESSMENT", "", assessment_index=0)])


def waiting_save(be: FakeBe | None = None) -> tuple[FakeBe, Any, Run]:
    llm = save_goal_llm()
    be = be or make_be(llm)
    conv = be.new_conversation()
    run = be.send_message(conv.conversation_id, GOAL_MSG, page_take_id="t-5")
    return be, llm, run


def waiting_reassess() -> tuple[FakeBe, Any, Run]:
    llm = reassess_llm({"script_used": True}, script_drafts(), F.answer([]))
    be = make_be(llm)
    be.use_rule_reviews()
    run = be.send_message(None, SCRIPT_MSG, page_take_id="t-5")
    return be, llm, run


def closed_text(first: Any, sentence: str) -> str:
    """이미 낸 문장(있으면) 뒤에 고정 문장을 이은 저장 글."""
    return "\n".join([*(c.text for c in first.message.claims), sentence])


def stored_texts(be: FakeBe, run: Run) -> list[str]:
    conv = be.conversations[run.conversation_id]
    return [m.text for m in conv.messages if m.run_id == run.run_id]


def test_ask_action_waits_for_approval_without_executing_or_storing() -> None:
    be, llm, run = waiting_save()
    conv = be.conversations[run.conversation_id]
    pend = run.pending
    assert run.status == "WAITING_APPROVAL" and pend is not None
    assert len(run.calls) == 1 and run.actions == [] and conv.goal is None
    # BE 는 NEEDS_ACTION 에서 args · args_hash 를 저장하고 대기 시작 시각을 남긴다
    action = ok(run.final).actions[0]
    assert pend.args == action.args and pend.args_hash == action.args_hash
    assert pend.args_hash == action_args_hash("SAVE_GOAL", pend.args)
    assert pend.started_ms == be.clock.clock_ms()
    # 카드는 AI 가 그린 그대로 화면에 돌려주고, 이미 낸 문장은 메시지로 저장하지 않은 채 기다린다(끝나야 저장)
    (card,) = cards(run, "ASK_CONFIRM")
    assert card["action_id"] == action.action_id and card["args_hash"] == pend.args_hash
    assert pend.card == card and stored_texts(be, run) == []
    assert len(llm.calls) >= 1


def test_approve_runs_stored_args_resumes_with_executed_hash_and_keeps_the_front() -> None:
    be, llm, run = waiting_save()
    conv = be.conversations[run.conversation_id]
    pend = run.pending
    assert pend is not None
    front = ok(run.final).message.text  # type: ignore[union-attr]
    n_actions = len(run.actions)
    out = be.approve(run.run_id, pend.action["action_id"], pend.args_hash)
    assert out is run and run.status == "DONE" and run.pending is None
    (rec,) = run.actions
    assert n_actions == 0 and rec.executed and rec.decision == "APPROVED"
    assert rec.action["args"] == pend.args  # 저장한 args 로만 실행
    # 이어 부른 요청의 결과에 BE 가 실행한 args 의 해시가 실린다
    result = run.calls[1].request["resume"]["action_results"][0]
    assert result["executed_args_hash"] == pend.args_hash
    assert conv.goal is not None and conv.goal["version"] == 1
    assert ok(run.final).message.text.startswith(front)  # type: ignore[union-attr]
    assert templates.ACTION_SUCCESS["SAVE_GOAL"] in stored_texts(be, run)[0]
    # 같은 승인의 재요청은 처음 결과 그대로(다시 실행하지 않는다)
    again = be.approve(run.run_id, pend.action["action_id"], pend.args_hash)
    assert again is run and len(run.actions) == 1 and len(run.calls) == 2
    assert conv.goal_version == 1


def test_fm_L2_40_hash_mismatch_is_409_and_runs_nothing() -> None:
    be, _llm, run = waiting_save()
    conv = be.conversations[run.conversation_id]
    pend = run.pending
    assert pend is not None
    aid = pend.action["action_id"]
    for call in (be.approve, be.reject):
        with pytest.raises(ApprovalError) as e:
            call(run.run_id, aid, "sha256:" + "0" * 64)
        assert e.value.http == 409
    assert run.status == "WAITING_APPROVAL" and run.pending is pend  # 대기 그대로
    assert run.actions == [] and conv.goal is None and len(run.calls) == 1
    # 남의 실행 · 없는 action_id 는 같은 404
    with pytest.raises(ApprovalError) as e:
        be.approve(run.run_id, aid, pend.args_hash, user_id="u-9")
    assert e.value.http == 404
    with pytest.raises(ApprovalError) as e:
        be.approve(run.run_id, "run-1:a9", pend.args_hash)
    assert e.value.http == 404
    # 맞는 해시로는 승인된다
    be.approve(run.run_id, aid, pend.args_hash)
    assert run.status == "DONE" and conv.goal is not None


def test_fm_L1_41_wrong_executed_args_hash_stops_the_run() -> None:
    """BE 가 승인한 것과 다른 인자를 실행했다고 보고하면(해시 불일치) AI 는 409 AGENT_STATE_INVALID 로 멈춘다."""
    be, _llm, run = waiting_save()
    pend = run.pending
    assert pend is not None

    def tamper(_b: FakeBe, _r: Run, rec: Any) -> None:
        rec.result["executed_args_hash"] = "sha256:" + "1" * 64

    be.before_resume = tamper
    be.approve(run.run_id, pend.action["action_id"], pend.args_hash)
    assert (run.calls[1].http_status, run.status, run.error_code) == (
        409,
        "FAILED",
        "AGENT_STATE_INVALID",
    )


def test_fm_L2_42_reject_ends_without_calling_ai_and_appends_the_fixed_sentence() -> None:
    be, llm, run = waiting_save()
    conv = be.conversations[run.conversation_id]
    pend = run.pending
    assert pend is not None
    first = ok(run.final)
    n_model = len(llm.calls)
    be.reject(run.run_id, pend.action["action_id"], pend.args_hash)
    assert run.status == "DONE" and len(llm.calls) == n_model and len(run.calls) == 1
    (rec,) = run.actions
    assert (rec.decision, rec.executed, rec.result["status"]) == ("REJECTED", False, "REJECTED")
    assert conv.goal is None and conv.goal_version == 0
    # 이미 낸 문장 뒤에 고정 문장, 판단 · 근거는 지우지 않는다
    (msg,) = [m for m in conv.messages if m.run_id == run.run_id]
    assert msg.text == closed_text(first, REJECT_TEXT)
    assert msg.claims[-1]["kind"] == "SYSTEM" and msg.status == "DONE"
    assert [a["assessment_id"] for a in msg.assessments] == [
        a.assessment_id for a in first.assessments
    ]
    assert run.closed_message is not None and run.closed_message["text"] == msg.text


def test_reject_reassess_uses_its_own_sentence_and_counts_nothing() -> None:
    be, _llm, run = waiting_reassess()
    pend = run.pending
    assert pend is not None
    before = official_reviews(be)
    first = ok(run.final)
    assert first.message is not None and first.message.text  # 다시 평가 앞에는 판단 문장이 있다
    be.reject(run.run_id, pend.action["action_id"], pend.args_hash)
    assert stored_texts(be, run) == [
        closed_text(first, templates.SystemText.REJECTED_REASSESS.value)
    ]
    assert be.review_calls == [] and be.reassess_today == {} and official_reviews(be) == before


def test_fm_L2_42_expiry_after_30_minutes_ends_without_calling_ai() -> None:
    be, llm, run = waiting_save()
    conv = be.conversations[run.conversation_id]
    pend = run.pending
    assert pend is not None
    n_model = len(llm.calls)
    be.clock.advance(APPROVAL_WAIT_MS / 1000 - 1)  # type: ignore[attr-defined]
    assert be.expire_due() == [] and run.status == "WAITING_APPROVAL"
    be.clock.advance(1)  # type: ignore[attr-defined]
    assert be.expire_due() == [run]
    assert run.status == "EXPIRED" and len(llm.calls) == n_model and len(run.calls) == 1
    assert run.actions[0].decision == "EXPIRED" and not run.actions[0].executed
    assert stored_texts(be, run)[0].endswith(templates.SystemText.APPROVAL_EXPIRED.value)
    assert conv.goal is None
    # 만료된 실행에 온 승인은 실행하지 않고 AGENT_EXPIRED
    with pytest.raises(ApprovalError) as e:
        be.approve(run.run_id, pend.action["action_id"], pend.args_hash)
    assert (e.value.http, e.value.code) == (409, "AGENT_EXPIRED") and conv.goal is None


def test_late_approval_after_the_deadline_is_expired_first() -> None:
    """만료 직전이 아니라 30분을 넘긴 뒤 온 승인은, 만료 확정을 먼저 하고 실행하지 않는다(L2-41)."""
    be, _llm, run = waiting_save()
    conv = be.conversations[run.conversation_id]
    pend = run.pending
    assert pend is not None
    be.clock.advance(APPROVAL_WAIT_MS / 1000 + 5)  # type: ignore[attr-defined]
    with pytest.raises(ApprovalError) as e:
        be.approve(run.run_id, pend.action["action_id"], pend.args_hash)
    assert e.value.code == "AGENT_EXPIRED" and run.status == "EXPIRED" and conv.goal is None


def test_fm_L2_43_new_message_while_waiting_cancels_then_accepts() -> None:
    be, llm, run = waiting_save()
    conv = be.conversations[run.conversation_id]
    pend = run.pending
    assert pend is not None
    first = ok(run.final)
    n_model = len(llm.calls)
    be.app.llm = llm2 = F.FakeLlm([F.plan(route="OUT_OF_SCOPE", steps=["범위 확인"])])
    run2 = be.send_message(conv.conversation_id, "오늘 날씨 어때?")
    # 앞 실행: 행동 CANCELLED · 실행 CANCELLED, 취소 문장이 이미 낸 문장 뒤에. 앞 실행을 위해 AI 를 부르지 않는다
    assert run.status == "CANCELLED" and run.actions[0].decision == "CANCELLED"
    assert len(llm.calls) == n_model and len(run.calls) == 1
    (old,) = [m for m in conv.messages if m.run_id == run.run_id]
    assert old.text == closed_text(first, templates.SystemText.APPROVAL_CANCELLED.value)
    # 새 메시지는 새 실행으로 접수되고 모델은 새 메시지 쪽에서만 불린다
    assert run2.run_id != run.run_id and run2.status in ("REFUSED", "DONE")
    assert len(llm2.calls) >= 1 and conv.goal is None
    # 취소가 확정된 뒤 온 승인은 실행하지 않는다
    with pytest.raises(ApprovalError) as e:
        be.approve(run.run_id, pend.action["action_id"], pend.args_hash)
    assert e.value.code == "AGENT_CANCELLED" and conv.goal is None and len(run.actions) == 1


def test_fm_L2_41_cancel_then_approve_runs_nothing_and_approve_then_cancel_keeps_the_result() -> (
    None
):
    be, _llm, run = waiting_save()
    conv = be.conversations[run.conversation_id]
    pend = run.pending
    assert pend is not None
    be.cancel(run.run_id)
    assert run.status == "CANCELLED"
    for call in (be.approve, be.reject):
        with pytest.raises(ApprovalError) as e:
            call(run.run_id, pend.action["action_id"], pend.args_hash)
        assert e.value.code == "AGENT_CANCELLED"
    assert conv.goal is None and [r.decision for r in run.actions] == ["CANCELLED"]
    # 승인이 먼저 선점했으면 뒤늦은 취소는 아무것도 바꾸지 않는다
    be2, _llm2, run2 = waiting_save()
    pend2 = run2.pending
    assert pend2 is not None
    be2.approve(run2.run_id, pend2.action["action_id"], pend2.args_hash)
    be2.cancel(run2.run_id)
    assert run2.status == "DONE" and be2.conversations[run2.conversation_id].goal is not None
    # 승인이 선점해 실행 중이면 같은 대화의 새 메시지는 취소하지 못한다(대기가 아니므로 대기 실행이 없다)
    assert be2.waiting_run(run2.conversation_id) is None


def test_settle_new_message_leaves_the_wait_and_approve_finishes_it() -> None:
    """평가 실행기의 결정: `NEW_MESSAGE`는 대기를 그대로 두고(다음 메시지가 취소), `APPROVE`는 끝까지 간다."""
    be, _llm, run = waiting_save()
    pend = run.pending
    assert pend is not None
    be.settle(run, "NEW_MESSAGE")
    assert run.status == "WAITING_APPROVAL" and run.pending is pend
    be.settle(run, "APPROVE")
    assert run.status == "DONE" and run.pending is None
    with pytest.raises(ValueError):
        be.settle(run, "MAYBE")


def test_reassess_daily_count_is_taken_once_at_approval() -> None:
    be, _llm, run = waiting_reassess()
    pend = run.pending
    assert pend is not None and be.reassess_today == {} and be.review_calls == []
    be.approve(run.run_id, pend.action["action_id"], pend.args_hash)
    assert be.reassess_today == {"u-3": 1} and run.reassess_count == 1
    be.approve(run.run_id, pend.action["action_id"], pend.args_hash)  # 같은 승인 재요청
    assert be.reassess_today == {"u-3": 1} and len(be.review_calls) == 2
    # 만료로 끝난 다시 평가는 세지 않는다
    be2, _llm2, run2 = waiting_reassess()
    be2.settle(run2, "EXPIRE")
    assert run2.status == "EXPIRED" and be2.reassess_today == {} and be2.review_calls == []


def test_every_ask_action_executes_only_with_a_decision_record() -> None:
    be, _llm, run = waiting_save()
    pend = run.pending
    assert pend is not None
    be.approve(run.run_id, pend.action["action_id"], pend.args_hash)
    assert all(r.decision == "APPROVED" for r in run.actions if r.executed)


# ── 선승인 ───────────────────────────────────────────────────────────────


def chosen_setup() -> tuple[FakeBe, Any, str]:
    """유창성이 이미 강점이라 목표 제안 카드가 나오고, 사용자 안을 골라 두 번째 메시지가 선승인 저장을 낸다."""
    msg = "필러를 줄이는 걸 목표로 잡아 줘"
    args = {**SAVE_ARGS, "metric": "filler_per_min", "slide_number": None}
    llm = F.FakeLlm(
        [
            F.plan(assessments=[request_draft(msg)]),
            F.tools(("save_goal", args), prefix="sg"),
            F.done(),
        ],
        answers=[FIRST],
        fixes=[F.fix({})],
    )
    be = make_be(llm)
    conv = be.new_conversation()
    first = be.send_message(conv.conversation_id, msg, page_take_id="t-5")
    (card,) = cards(first, "GOAL_PROPOSAL")
    be.app.llm = F.FakeLlm([F.done()], answers=[F.answer([])], fixes=[F.fix({})])
    return be, conv, card["proposal_id"]


def send_choice(be: FakeBe, conv: Any, pid: str) -> Run:
    return be.send_message(
        conv.conversation_id,
        "말씀하신 안으로 목표를 잡아 줘",
        page_take_id="t-5",
        choice={"proposal_id": pid, "option": "USER"},
    )


def test_preapproved_with_matching_hash_runs_without_card_or_wait() -> None:
    be, conv, pid = chosen_setup()
    run = send_choice(be, conv, pid)
    assert run.status == "DONE" and run.pending is None
    (rec,) = run.actions
    assert rec.decision == "PREAPPROVED" and rec.executed
    assert cards(run, "ASK_CONFIRM") == [] and conv.goal is not None
    action = run.calls[0].final["actions"][0]  # type: ignore[index]
    assert action["autonomy"] == "ASK" and action["preapproved"]["proposal_id"] == pid
    # BE 가 실제 args.goal 에서 다시 계산한 내용 해시가 저장된 제안의 것과 같다
    assert action["preapproved"]["content_hash"] == content_hash(action["args"]["goal"])
    assert rec.result["executed_args_hash"] == action["args_hash"]


@pytest.mark.parametrize("tamper", ["goal", "proposal_id", "content_hash"])
def test_fm_L1_44_preapproved_with_a_different_goal_or_proposal_runs_nothing(tamper: str) -> None:
    be, conv, pid = chosen_setup()

    def change(b: FakeBe, r: Run, action: dict[str, Any]) -> None:
        if tamper == "goal":
            action["args"]["goal"]["slide_number"] = 2
            action["args_hash"] = action_args_hash(action["type"], action["args"])
        elif tamper == "proposal_id":
            action["preapproved"]["proposal_id"] = "other"
        else:
            action["preapproved"]["content_hash"] = "0" * 16

    be.before_action = change
    run = send_choice(be, conv, pid)
    (rec,) = run.actions
    assert not rec.executed and rec.decision is None
    assert (rec.result["status"], rec.result["error"]["code"]) == ("FAILED", "PREAPPROVAL_MISMATCH")
    assert conv.goal is None and conv.goal_version == 0


# ── 버튼 · 자율 등급 위조 ────────────────────────────────────────────────


def test_start_practice_button_is_never_executed_and_moves_only_on_click() -> None:
    msg = "바로 발표할래"
    d = F.draft(msg, type_="REQUEST", claim_op=None, polarity="NEUTRAL")
    llm = F.FakeLlm(
        [F.plan(route="ANSWER_DIRECT", steps=[], assessments=[d])],
        answers=[F.answer([], assessments=[d], start_practice={"authorized_by": msg})],
    )
    be = make_be(llm)
    conv = be.new_conversation()
    run = be.send_message(conv.conversation_id, msg, page_take_id="t-5")
    assert run.status == "DONE" and run.actions == [] and run.pending is None
    (action,) = ok(run.final).actions
    assert (action.type, action.autonomy) == ("START_PRACTICE", "BUTTON")
    assert [c["type"] for c in cards(run)] == ["START_PRACTICE"]
    assert conv.practice_moves == []  # 카드를 냈을 뿐 화면은 그대로
    move = be.click_practice(run)  # 사용자가 버튼을 누름: 이동 기록만 남는다
    assert move == {"run_id": run.run_id, "pitch_id": "p-1"} and conv.practice_moves == [move]
    assert run.actions == []


def test_fm_L1_45_take_reviewed_run_never_executes_save_even_if_asked() -> None:
    """사용자 말이 없는 자동 확인 실행은 저장 행동을 실행하지 않는다(AI 가 내지 않고, BE 도 거절)."""
    be, _llm, run = waiting_save()
    pend = run.pending
    assert pend is not None
    auto = Run("run-9", run.conversation_id, "u-3", "TAKE_REVIEWED", original={}, history={})
    conv = be.conversations[run.conversation_id]
    result = be._save_goal(auto, conv, pend.action)
    assert (result["status"], result["error"]["code"]) == ("FAILED", "NOT_ALLOWED")
    assert conv.goal is None
