"""승인 흐름 코어 시험 — `ASK` 행동 앞 답 쓰기 · 확인 카드 · 재개 뒤 앞부분 유지 · 선승인 · 버튼 카드 (기능 문서 5-5 · 5-8 · 6-1 4번 · 6-2).

실제 모델 없이 정해 둔 응답(tests/unit/fakes.py)을 쓴다. 행동 모양 · 해시의 단위 시험은 test_actions.py,
최종 출력 판별 검사는 test_contract_schemas.py 에 있다.
"""

from __future__ import annotations

import base64
import copy
import json
from datetime import UTC, datetime
from typing import Any

import pytest

from chat_agent import templates
from chat_agent.actions import action_args_hash
from chat_agent.config import DEFAULT_CONFIG
from chat_agent.llm_types import LlmCallFailed, LlmRefused, LlmTimeout
from chat_agent.schemas import FinalData

from . import fakes as F
from .test_actions import _proposal_request
from .test_turn import COMPARE, final_ok, resume_request, roles

GOAL_MSG = "3장에서 왜 자꾸 시선이 떨어져? 그걸 목표로 잡고 다음 연습 준비해 줘"
SAVE = (
    "save_goal",
    {
        "metric": "audience_ratio",
        "slide_number": 3,
        "base_take_id": "t-5",
        "authorized_by": "그걸 목표로 잡고",
        "reason": "3장 시선 목표",
    },
)
REASSESS = (
    "reassess",
    {"take_id": "t-5", "change": {"script_used": True}, "reason": "대본 조건"},
)


def drafts() -> list[dict[str, Any]]:
    return [
        F.draft(
            "3장에서 왜 자꾸 시선이 떨어져",
            claim_op="ISSUE",
            dimension="audience_gaze",
            metric="audience_ratio",
            slide_number=3,
            polarity="NEGATIVE",
        ),
        F.draft(
            "그걸 목표로 잡고 다음 연습 준비해 줘",
            type_="REQUEST",
            claim_op=None,
            polarity="NEUTRAL",
        ),
    ]


def record_answer(r: Any) -> Any:
    """승인 대기 앞 답: 판단 설명은 비우고(첫 문장은 코드) 기록 문장 하나."""
    eid = roles(r)["current"]
    return F.answer(
        [
            F.claim("ASSESSMENT", "", assessment_index=0),
            F.claim("RECORD", "Take 5의 3장 청중 시선 비율은 28%예요.", evidence_ids=[eid]),
        ]
    )


def save_script(**kw: Any) -> list[Any]:
    return [
        F.plan(assessments=drafts()),
        F.tools(COMPARE),
        F.tools(SAVE, prefix="sg"),
        F.done(),
    ]


def first_request(message: str = GOAL_MSG) -> dict[str, Any]:
    return F.with_loaded(F.request(user_message=message), "t-4")


#: 목표 저장과 다시 평가(조건: 내 기준)를 함께 바라는 말. 다시 평가는 조건 없는 측정값 이야기만으로는 내지 않는다(6-6)
GOAL_AND_REASSESS_MSG = GOAL_MSG + " 내 기준으로 다시 평가도 해 줘"


def pause(llm: Any | None = None, *, req: dict[str, Any] | None = None):  # noqa: ANN201
    req = req or first_request()
    llm = llm or F.FakeLlm(save_script(), answers=[record_answer, F.answer([])], fixes=[F.fix({})])
    _, _, data = F.run_turn(req, llm)
    return req, llm, data


def saved_result(action: dict[str, Any], **kw: Any) -> list[dict[str, Any]]:
    goal = {
        **action["args"]["goal"],
        "version": 1,
        "status": "OPEN",
        "created_at": "2026-11-04T10:05:00+09:00",
    }
    return [
        {
            "action_id": action["action_id"],
            "type": "SAVE_GOAL",
            "status": "SUCCEEDED",
            "data": {"goal": goal},
            "error": None,
            "executed_args_hash": action["args_hash"],
            **kw,
        }
    ]


def payload_of(state: dict[str, Any]) -> dict[str, Any]:
    raw = state["payload"] + "=" * (-len(state["payload"]) % 4)
    return json.loads(base64.urlsafe_b64decode(raw))


# ── ASK 행동 앞 답 쓰기 (6-1 4번) ────────────────────────────────────────────


def test_ask_action_writes_the_answer_first_then_stops_with_card_and_one_action() -> None:
    req, llm, data = pause()
    fd = final_ok(data)
    assert fd.status == "NEEDS_ACTION"
    # 행동은 하나이고 ASK 등급 + args_hash(행동 type 과 args 의 해시)
    (action,) = fd.actions
    assert (action.type, action.autonomy, action.preapproved) == ("SAVE_GOAL", "ASK", None)
    assert action.args_hash == action_args_hash("SAVE_GOAL", action.args)
    # 그때까지 확인한 것으로 쓴 답 문장(판단 첫 문장은 코드 템플릿)과 확인 카드를 함께 낸다
    assert fd.message is not None
    kinds = [c.kind for c in fd.message.claims]
    assert kinds == ["ASSESSMENT", "RECORD"]
    assert fd.message.claims[0].text.startswith("말씀하신 '3장에서 왜 자꾸 시선이 떨어져'는")
    assert fd.message.claims[1].text == "Take 5의 3장 청중 시선 비율은 28%예요."
    assert fd.message.text == "\n".join(c.text for c in fd.message.claims)
    (card,) = fd.message.cards
    assert card.type == "ASK_CONFIRM"
    assert (card.action_id, card.action_type, card.args_hash) == (
        action.action_id,
        "SAVE_GOAL",
        action.args_hash,
    )
    assert card.after == action.args["goal"] and card.before is None
    # 이 답 쓰기는 답 쓰기 몫을 쓴다: 조사 3번 + 답 쓰기 1번(고칠 문장이 없어 고치기는 안 씀)
    assert [c.purpose for c in llm.calls] == ["RESEARCH", "RESEARCH", "RESEARCH", "ANSWER"]
    assert fd.budget_used.model_calls == 4
    # 판단은 이 답에 들어가고(별도 interim 값 없음) 낸 판단의 근거는 evidence 에 있다
    ids = {e.evidence_id for e in fd.evidence}
    assert fd.assessments and all(set(c.evidence_ids) <= ids for c in fd.message.claims)
    assert "interim_message" not in data


def test_confirm_card_expires_30_minutes_after_issue_and_state_lasts_35() -> None:
    clock = F.Clock()
    req = first_request()
    _, _, data = F.run_turn(
        req,
        F.FakeLlm(save_script(), answers=[record_answer], fixes=[F.fix({})]),
        clock=clock,
    )
    fd = final_ok(data)
    (card,) = fd.message.cards  # type: ignore[union-attr]
    start = clock.ms
    exp = datetime.fromisoformat(card.expires_at)
    assert exp.tzinfo is not None
    assert int(exp.astimezone(UTC).timestamp() * 1000) - start == 30 * 60 * 1000
    p = payload_of(fd.state)  # type: ignore[arg-type]
    assert p["expires_at"] - p["issued_at"] == DEFAULT_CONFIG.state_ttl_ask_seconds * 1000
    assert DEFAULT_CONFIG.state_ttl_ask_seconds == 35 * 60


def test_read_action_state_keeps_the_ordinary_ten_minutes() -> None:
    from .test_turn import fetch_take_script

    _, _, data = F.run_turn(
        F.request(user_message="Take 4보다 Take 5에서 3장 시선이 좋아졌어?"),
        F.FakeLlm(fetch_take_script()),
    )
    fd = final_ok(data)
    assert fd.actions[0].autonomy == "AUTO" and fd.actions[0].args_hash is None
    p = payload_of(fd.state)  # type: ignore[arg-type]
    assert p["expires_at"] - p["issued_at"] == DEFAULT_CONFIG.state_ttl_seconds * 1000


def test_reassess_pauses_with_sentence_and_card_without_authorization_check() -> None:
    msg = "대본 보고 한 발표였어. 다시 평가해 줘"
    d = [
        F.draft(
            "대본 보고 한 발표였어",
            claim_op="VALUE",
            dimension="script_independence",
            metric="script_mode",
            polarity="POSITIVE",
        ),
        F.draft("다시 평가해 줘", type_="REQUEST", claim_op=None, polarity="NEUTRAL"),
    ]
    llm = F.FakeLlm(
        [F.plan(assessments=d), F.tools(REASSESS, prefix="ra")],
        answers=[F.answer([F.claim("ASSESSMENT", "", assessment_index=0)])],
        fixes=[F.fix({})],
    )
    _, _, data = F.run_turn(F.request(user_message=msg), llm)
    fd = final_ok(data)
    (action,) = fd.actions
    assert (action.type, action.autonomy, action.authorized_by) == ("REASSESS", "ASK", None)
    (card,) = fd.message.cards  # type: ignore[union-attr]
    assert card.action_type == "REASSESS" and card.args_hash == action.args_hash
    assert "참고 결과이고 공식 결과는 그대로예요" in card.notes
    assert fd.message.claims and fd.message.claims[0].kind == "ASSESSMENT"  # type: ignore[union-attr]
    assert templates.SystemText.REASSESS_HYPOTHETICAL.value not in fd.message.text  # type: ignore[union-attr]


def test_model_failure_before_ask_still_shows_code_sentences_and_card() -> None:
    """답 쓰기가 안 돼도(한도 · 오류) 코드가 확정한 판단 문장과 확인 카드로 멈춘다."""
    llm = F.FakeLlm(save_script(), answers=[LlmTimeout()], fixes=[F.fix({})])
    _, _, data = pause(llm)
    fd = final_ok(data)
    assert fd.status == "NEEDS_ACTION" and fd.actions[0].autonomy == "ASK"
    assert fd.message is not None and [c.kind for c in fd.message.claims] == ["ASSESSMENT"]
    assert [c.type for c in fd.message.cards] == ["ASK_CONFIRM"]


@pytest.mark.parametrize("failure", [LlmTimeout(), LlmRefused(), LlmCallFailed()])
def test_fm_L1_42_answer_writing_failure_kinds_still_pause_with_the_card(failure: Any) -> None:
    """답 쓰기가 시간 초과 · 거절 · 오류로 실패해도 승인 대기를 막지 않는다: 모델 문장 없이 코드 문장과 카드로 멈춘다(L1-42)."""
    llm = F.FakeLlm(save_script(), answers=[failure], fixes=[F.fix({})])
    _, _, data = pause(llm)
    fd = final_ok(data)
    assert fd.status == "NEEDS_ACTION" and [a.autonomy for a in fd.actions] == ["ASK"]
    assert fd.message is not None
    assert all(
        c.kind in ("ASSESSMENT", "SYSTEM") for c in fd.message.claims
    )  # 모델이 쓴 RECORD 문장이 없다
    assert [c.type for c in fd.message.cards] == ["ASK_CONFIRM"]
    assert fd.message.cards[0].args_hash == fd.actions[0].args_hash  # type: ignore[union-attr]


def test_fm_L1_43_card_that_cannot_be_drawn_gives_no_action(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """확인 카드를 그릴 수 없으면 승인받을 수 없으니 행동을 내지 않고 PARTIAL 로 끝낸다(L1-43)."""
    from chat_agent import turn as turn_mod

    monkeypatch.setattr(turn_mod.act, "ask_confirm_card", lambda *a, **k: None)
    _, _, data = pause()
    fd = final_ok(data)
    assert fd.status == "PARTIAL" and fd.actions == [] and fd.state is None
    assert fd.message is not None
    assert templates.SystemText.ACTION_UNCONFIRMED.value in fd.message.text
    assert all(c.type != "ASK_CONFIRM" for c in fd.message.cards)


def test_ask_after_unauthorized_request_becomes_goal_proposal_not_a_card() -> None:
    """허용 근거 검사에 못 걸리면 확인 카드가 아니라 목표 제안 카드(GOAL_PROPOSAL)로 낮춘다(6-2)."""
    msg = "3장에서 왜 자꾸 시선이 떨어져? 목표 얘기 좀 해 보자"
    save = ("save_goal", {**SAVE[1], "authorized_by": "목표 얘기 좀 해 보자"})
    llm = F.FakeLlm(
        [F.plan(assessments=drafts()[:1]), F.tools(COMPARE), F.tools(save, prefix="sg"), F.done()],
        answers=[F.answer([F.claim("ASSESSMENT", "", assessment_index=0)])],
        fixes=[F.fix({})],
    )
    _, _, data = F.run_turn(F.with_loaded(F.request(user_message=msg), "t-4"), llm)
    fd = final_ok(data)
    assert fd.status == "DONE" and fd.actions == []
    kinds = [c.type for c in fd.message.cards]  # type: ignore[union-attr]
    assert "GOAL_PROPOSAL" in kinds and "ASK_CONFIRM" not in kinds


def test_take_reviewed_turn_never_asks() -> None:
    req = F.request(
        run__trigger="TAKE_REVIEWED",
        user_message=None,
        review_event={"take_id": "t-5", "review_revision": 1, "goal_version": 0},
    )
    llm = F.FakeLlm([])
    _, _, data = F.run_turn(req, llm)
    fd = final_ok(data)
    assert fd.actions == [] and llm.calls == []
    assert fd.message is None or all(c.type != "ASK_CONFIRM" for c in fd.message.cards)


# ── 재개 뒤 앞부분 유지 (5-5 · 5-6) ──────────────────────────────────────────


def test_resumed_answer_starts_with_the_sentences_shown_before_the_pause() -> None:
    req, llm, data = pause()
    paused = final_ok(data)
    said = [c.model_dump(mode="json") for c in paused.message.claims]  # type: ignore[union-attr]
    action = data["actions"][0]
    # 서명 상태가 낸 문장 · 판단을 담는다
    loop = payload_of(paused.state)["loop"]  # type: ignore[arg-type]
    assert loop["emitted"] == said
    assert [a["assessment_id"] for a in loop["exported"]] == [
        a.assessment_id for a in paused.assessments
    ]
    llm2 = F.FakeLlm([F.done()], answers=[F.answer([])], fixes=[F.fix({})])
    req2 = resume_request(req, data, saved_result(action))
    req2["context"]["conversation_goal"] = None
    _, _, d2 = F.run_turn(req2, llm2)
    fd = final_ok(d2)
    assert fd.status == "DONE" and fd.state is None
    claims = [c.model_dump(mode="json") for c in fd.message.claims]  # type: ignore[union-attr]
    # 앞부분은 멈추기 전에 낸 문장 그대로(글자 · 종류 · 근거까지), 그 뒤에 행동 결과 문장(코드 템플릿)
    assert claims[: len(said)] == said
    assert claims[len(said)]["kind"] == "ACTION_RESULT"
    assert claims[len(said)]["text"] == templates.ACTION_SUCCESS["SAVE_GOAL"]
    assert fd.message.text == "\n".join(c["text"] for c in claims)  # type: ignore[union-attr]
    # 낸 판단은 최종 답에도 그대로 있고 다시 확정하지 않는다(중복 없음)
    assert [a.assessment_id for a in fd.assessments] == [
        a.assessment_id for a in paused.assessments
    ]
    # 모든 문장이 가리키는 근거는 최종 답의 evidence 에 있다
    ids = {e.evidence_id for e in fd.evidence}
    assert all(set(c["evidence_ids"]) <= ids for c in claims)
    assert [c.type for c in fd.message.cards] == ["GOAL_SAVED"]  # 확인 카드는 최종 답에 남지 않는다
    # 이어 부른 호출은 이미 낸 문장을 모델에 알려 되풀이하지 않게 한다
    answer_req = next(c for c in llm2.calls if c.purpose == "ANSWER")
    blob = json.dumps(answer_req.messages, ensure_ascii=False)
    assert "already_said" in blob and "Take 5의 3장 청중 시선 비율은 28%예요." in blob


def test_resumed_model_repeating_a_shown_sentence_is_dropped() -> None:
    req, _, data = pause()
    again = F.answer(
        [
            F.claim(
                "RECORD",
                "Take 5의 3장 청중 시선 비율은 28%예요.",
                evidence_ids=[data["message"]["claims"][1]["evidence_ids"][0]],
            )
        ]
    )
    req2 = resume_request(req, data, saved_result(data["actions"][0]))
    _, _, d2 = F.run_turn(req2, F.FakeLlm([F.done()], answers=[again], fixes=[F.fix({})]))
    texts = [c["text"] for c in final_ok(d2).message.model_dump(mode="json")["claims"]]  # type: ignore[union-attr]
    assert texts.count("Take 5의 3장 청중 시선 비율은 28%예요.") == 1


def test_resume_without_answer_slots_ends_without_model_but_keeps_the_front() -> None:
    """남은 호출 수로 답 쓰기 · 고치기 몫을 남길 수 없으면 모델 없이 끝내되 앞부분은 그대로 싣는다(8-3)."""
    req, _, data = pause()
    said = data["message"]["claims"]
    req2 = resume_request(req, data, saved_result(data["actions"][0]))
    req2["budget_used"] = {**req2["budget_used"], "model_calls": 17}
    llm2 = F.FakeLlm([])
    _, _, d2 = F.run_turn(req2, llm2)
    fd = final_ok(d2)
    assert llm2.calls == []  # 모델 없음
    assert [c.model_dump(mode="json") for c in fd.message.claims[: len(said)]] == said  # type: ignore[union-attr]
    assert fd.status in ("PARTIAL", "DONE")


def test_second_pause_after_resume_carries_the_front_sentences() -> None:
    """승인 뒤 읽기 행동(AUTO)으로 다시 멈추면 message 에 이미 낸 문장을 싣는다. 아직 낸 문장이 없을 때만 null 이다(5-5)."""
    req, _, data = pause()
    said = data["message"]["claims"]
    req2 = resume_request(req, data, saved_result(data["actions"][0]))
    fetch3 = ("fetch_take", {"take_id": "t-3", "reason": "Take 3"})
    _, _, d2 = F.run_turn(req2, F.FakeLlm([F.tools(fetch3, prefix="f")]))
    fd = final_ok(d2)
    assert fd.status == "NEEDS_ACTION" and fd.actions[0].autonomy == "AUTO"
    assert fd.message is not None
    assert [c.model_dump(mode="json") for c in fd.message.claims] == said
    assert fd.message.cards == []  # 확인 카드는 ASK 행동 앞에서만


def test_second_ask_in_one_run_stacks_on_the_front_and_the_first_result() -> None:
    """한 실행에 승인 대기는 최대 2개(저장 1 · 다시 평가 1)이고 한 번에 하나다. 둘째 멈춤은 앞 문장 · 첫 행동 결과 뒤에 카드를 낸다."""
    req, _, data = pause(req=first_request(GOAL_AND_REASSESS_MSG))
    said = data["message"]["claims"]
    req2 = resume_request(req, data, saved_result(data["actions"][0]))
    llm2 = F.FakeLlm([F.tools(REASSESS, prefix="ra")], answers=[F.answer([])], fixes=[F.fix({})])
    _, _, d2 = F.run_turn(req2, llm2)
    fd = final_ok(d2)
    (action,) = fd.actions
    assert fd.status == "NEEDS_ACTION" and (action.type, action.autonomy) == ("REASSESS", "ASK")
    assert fd.message is not None
    claims = [c.model_dump(mode="json") for c in fd.message.claims]
    assert claims[: len(said)] == said
    assert [c["kind"] for c in claims[len(said) :]] == ["ACTION_RESULT"]
    assert [c.type for c in fd.message.cards] == ["ASK_CONFIRM"]
    assert fd.message.cards[0].action_id == action.action_id  # type: ignore[union-attr]
    # 보인 문장(첫 행동 결과 문장 포함)은 서명 상태에 담겨 다음 이어 부르기에서 다시 만들지 않는다
    assert payload_of(fd.state)["loop"]["emitted"] == claims  # type: ignore[arg-type]


# ── 해시 묶기 (5-8) ─────────────────────────────────────────────────────────


def test_executed_args_hash_mismatch_or_missing_is_409() -> None:
    req, _, data = pause()
    action = data["actions"][0]
    other = action_args_hash("SAVE_GOAL", {"expected_goal_version": 0, "goal": {"x": 1}})
    for results in (
        saved_result(action, executed_args_hash=other),  # 사용자가 본 카드와 다른 인자로 실행됨
        saved_result(action, executed_args_hash=None),  # 성공인데 해시가 없음
    ):
        status, _, body = F.run_turn(resume_request(req, data, results), F.FakeLlm([]))
        assert status == 409 and body["error"]["code"] == "AGENT_STATE_INVALID"
    status, _, _ = F.run_turn(
        resume_request(req, data, saved_result(action)),
        F.FakeLlm([F.done()], answers=[F.answer([])], fixes=[F.fix({})]),
    )
    assert status == 200


# ── 고른 제안 · 연습 버튼 ────────────────────────────────────────────────────


def test_chosen_proposal_saves_with_preapproved_and_no_card_or_model() -> None:
    req, card = _proposal_request()
    llm = F.FakeLlm([])
    _, _, data = F.run_turn(req, llm)
    fd = final_ok(data)
    (action,) = fd.actions
    sp = req["context"]["selected_proposal"]
    assert fd.status == "NEEDS_ACTION" and (action.type, action.autonomy) == ("SAVE_GOAL", "ASK")
    assert action.preapproved is not None
    assert action.preapproved.model_dump() == {
        "by": "CHOICE",
        "proposal_id": sp["proposal_id"],
        "content_hash": sp["content_hash"],
    }
    assert action.args_hash == action_args_hash("SAVE_GOAL", action.args)
    # 확인 카드도 승인 대기도 없다: 문장 없는 message, 유효기간은 보통 10분
    assert fd.message is not None and fd.message.claims == [] and fd.message.cards == []
    p = payload_of(fd.state)  # type: ignore[arg-type]
    assert p["expires_at"] - p["issued_at"] == DEFAULT_CONFIG.state_ttl_seconds * 1000
    assert llm.calls == []  # 계획 · 모델 없이 바로
    assert card["proposal_id"] == sp["proposal_id"]


def test_practice_move_is_always_a_button_card_even_without_a_request_quote() -> None:
    """연습 이동은 허용 근거와 상관없이 [연습 준비로] 버튼 카드 + BUTTON 행동이다(자동 이동 없음, 6-2)."""
    msg = "바로 발표할래"
    req_draft = F.draft(msg, type_="REQUEST", claim_op=None, polarity="NEUTRAL")
    for quote in (msg, None):  # 요청한 말 · 연습을 권하기만 한 경우
        llm = F.FakeLlm(
            [F.plan(route="ANSWER_DIRECT", steps=[], assessments=[req_draft])],
            answers=[
                F.answer([], assessments=[req_draft], start_practice={"authorized_by": quote})
            ],
        )
        _, _, data = F.run_turn(F.request(user_message=msg), llm)
        fd = final_ok(data)
        assert fd.status == "DONE"
        (action,) = fd.actions
        assert (action.type, action.autonomy, action.args_hash) == (
            "START_PRACTICE",
            "BUTTON",
            None,
        )
        (card,) = [c for c in fd.message.cards if c.type == "START_PRACTICE"]  # type: ignore[union-attr]
        assert "auto" not in card.model_dump()


def test_final_output_validation_rejects_ask_without_card() -> None:
    """코드가 만든 응답은 판별 검사를 통과하고, 카드를 빼면 거절된다(5-5)."""
    _, _, data = pause()
    final_ok(data)
    broken = copy.deepcopy(data)
    broken["message"]["cards"] = []
    try:
        FinalData.model_validate(broken)
    except ValueError:
        pass
    else:
        raise AssertionError("확인 카드 없는 ASK 행동이 통과했다")


# ── 승인 전 답 쓰기: 검사에 걸린 문장 (6-1 4번 · 6-9) ────────────────────────────

GOOD = "Take 5의 3장 청중 시선 비율은 28%예요."
BAD = "Take 5의 3장 청중 시선 비율은 99%예요."


def bad_record_answer(r: Any) -> Any:
    """근거 없는 수치(99%)를 쓴 답: 검사에 걸린다."""
    eid = roles(r)["current"]
    return F.answer(
        [
            F.claim("ASSESSMENT", "", assessment_index=0),
            F.claim("RECORD", BAD, evidence_ids=[eid]),
        ]
    )


def test_ask_pause_fixes_a_failed_sentence_once_before_the_card() -> None:
    """검사에 걸린 문장은 한 번 고쳐 통과하면 그 문장이 카드 앞에 나간다. 고치기도 한 번의 모델 호출이다."""
    llm = F.FakeLlm(save_script(), answers=[bad_record_answer], fixes=[F.fix({1: GOOD})])
    _, _, data = pause(llm)
    fd = final_ok(data)
    assert fd.status == "NEEDS_ACTION" and fd.message is not None
    assert [c.text for c in fd.message.claims][1:] == [GOOD]
    assert BAD not in fd.message.text
    assert [c.purpose for c in llm.calls] == ["RESEARCH"] * 3 + ["ANSWER", "FIX"]
    assert fd.budget_used.model_calls == 5
    assert [c.type for c in fd.message.cards] == ["ASK_CONFIRM"]


def test_ask_pause_drops_a_sentence_that_still_fails_after_the_fix() -> None:
    """고친 문장도 걸리면 버린다: 걸린 수치는 카드 앞에 나가지 않고, 고치기는 한 번뿐이다."""
    llm = F.FakeLlm(save_script(), answers=[bad_record_answer], fixes=[F.fix({1: BAD})])
    _, _, data = pause(llm)
    fd = final_ok(data)
    assert fd.status == "NEEDS_ACTION" and fd.message is not None
    assert [c.kind for c in fd.message.claims] == ["ASSESSMENT"]  # 코드 판단 문장만 남는다
    assert "99%" not in fd.message.text
    assert [c.purpose for c in llm.calls] == ["RESEARCH"] * 3 + ["ANSWER", "FIX"]
    assert [c.type for c in fd.message.cards] == ["ASK_CONFIRM"]
    # 서명 상태에도 버린 문장은 실리지 않는다
    emitted = payload_of(fd.state)["loop"]["emitted"]  # type: ignore[arg-type]
    assert "99%" not in json.dumps(emitted, ensure_ascii=False)


# ── 확인 카드: 바꾸기 전 조건을 확인할 수 없거나 표시할 수 없으면 행동을 내지 않는다 (6-6) ──


def reassess_turn(mutate: Any, change: dict[str, Any] | None = None) -> Any:
    msg = "내 기준으로 다시 평가해 줘"
    d = [F.draft("다시 평가해 줘", type_="REQUEST", claim_op=None, polarity="NEUTRAL")]
    args = {"take_id": "t-5", "change": change or {"script_used": True}, "reason": "조건"}
    req = F.request(user_message=msg)
    mutate(req)
    llm = F.FakeLlm(
        [F.plan(assessments=d), F.tools(("reassess", args), prefix="ra")],
        answers=[F.answer([])],
        fixes=[F.fix({})],
    )
    _, _, data = F.run_turn(req, llm)
    return final_ok(data)


def test_reassess_without_a_confirmable_before_condition_ends_without_action() -> None:
    """Take 뒤에 평가 기준 판이 바뀌어 그 Take 의 옛 조건을 알 수 없으면 카드도 행동도 내지 않는다."""

    def newer_standard(req: dict[str, Any]) -> None:
        req["context"]["pitch"]["criteria"]["standard_version"] = 2

    fd = reassess_turn(lambda r: None)
    assert fd.status == "NEEDS_ACTION"  # 같은 판이면 지금 기준이 그 Take 의 조건이라 카드가 선다
    fd = reassess_turn(newer_standard)
    assert fd.status == "PARTIAL" and fd.actions == [] and fd.state is None
    assert fd.message is not None and not fd.message.cards
    assert templates.SystemText.ACTION_UNCONFIRMED.value in fd.message.text


def test_reassess_card_with_a_condition_text_that_fails_display_check_is_not_drawn() -> None:
    """카드에 싣는 기준 문구(display)가 표시 문자열 검사(링크 · 주입 · 개인정보)에 걸리면 카드도 행동도 없다."""
    link = {"targets": [{"display": "https://example.com 에서 확인"}]}
    fd = reassess_turn(lambda r: None, link)
    assert fd.status == "PARTIAL" and fd.actions == []
    plain = {"targets": [{"display": "자연스럽게 말하기"}]}
    fd = reassess_turn(lambda r: None, plain)
    assert fd.status == "NEEDS_ACTION"
    card = fd.message.cards[0]  # type: ignore[union-attr]
    assert card.after["targets"] == [  # 숫자 목표가 없는 기준은 문구로 카드에 보인다
        {"display": "자연스럽게 말하기", "metric": None, "operator": None, "value": None}
    ]


# ── 모델 없이 끝나는 길 (8-3 · 5-5) ──────────────────────────────────────────


def test_resume_without_room_keeps_the_front_and_tells_the_approved_result_with_its_card() -> None:
    """남은 호출이 모자라 모델 없이 끝내도 저장 성공 문장(코드 템플릿)과 GOAL_SAVED 카드를 앞 문장 뒤에 싣는다."""
    req, _, data = pause()
    said = data["message"]["claims"]
    req2 = resume_request(req, data, saved_result(data["actions"][0]))
    req2["budget_used"] = {**req2["budget_used"], "model_calls": 17}
    llm2 = F.FakeLlm([])
    _, _, d2 = F.run_turn(req2, llm2)
    fd = final_ok(d2)
    assert llm2.calls == [] and "error" not in d2
    assert fd.status == "PARTIAL" and fd.actions == [] and fd.state is None
    claims = [c.model_dump(mode="json") for c in fd.message.claims]  # type: ignore[union-attr]
    assert claims[: len(said)] == said
    assert claims[len(said)]["kind"] == "ACTION_RESULT"
    assert claims[len(said)]["text"] == templates.ACTION_SUCCESS["SAVE_GOAL"]
    assert templates.SystemText.LIMIT_PARTIAL.value in fd.message.text  # type: ignore[union-attr]
    assert [c.type for c in fd.message.cards] == ["GOAL_SAVED"]  # type: ignore[union-attr]


def _resume_with_state_turn_count(req: Any, data: dict[str, Any], turn_count: int) -> Any:
    """서명 상태의 호출 번호를 바꿔 다시 서명한다(7번째 호출 한도 시험)."""
    from chat_agent import state as st

    p = payload_of(data["state"])
    p["turn_count"] = turn_count
    return {**data, "state": st.sign(st.StatePayload.model_validate(p), F.SECRET)}


def _limit_cases() -> list[str]:
    return ["TIME", "TURN_CALLS", "MODEL_CALLS"]


def _resume_at_limit(which: str) -> tuple[Any, dict[str, Any], Any]:
    req, _, data = pause()
    if which == "TURN_CALLS":
        data = _resume_with_state_turn_count(req, data, 6)  # 이어 부르면 7번째
    req2 = resume_request(req, data, saved_result(data["actions"][0]))
    if which == "TIME":
        req2["budget_used"] = {**req2["budget_used"], "elapsed_ms": 120_000}
    if which == "MODEL_CALLS":
        req2["budget_used"] = {**req2["budget_used"], "model_calls": 17}
    return req2, data, F.FakeLlm([])


def test_resume_at_any_limit_still_tells_the_approved_result_with_its_card() -> None:
    """시간 소진 · 7번째 호출 · 답 쓰기 몫 부족 어느 한도로 끝나도 앞 문장 → 결과 문장 → 한도 안내 순서이고 GOAL_SAVED 카드가 남는다."""
    for which in _limit_cases():
        req2, data, llm2 = _resume_at_limit(which)
        said = payload_of(data["state"])["loop"]["emitted"]
        _, _, d2 = F.run_turn(req2, llm2)
        fd = final_ok(d2)
        assert llm2.calls == [], which
        assert fd.status == "PARTIAL" and fd.actions == [], which
        claims = [c.model_dump(mode="json") for c in fd.message.claims]  # type: ignore[union-attr]
        assert claims[: len(said)] == said, which
        assert claims[len(said)]["kind"] == "ACTION_RESULT", which
        assert claims[len(said)]["text"] == templates.ACTION_SUCCESS["SAVE_GOAL"], which
        notice = (
            templates.SystemText.TURN_LIMIT
            if which == "TURN_CALLS"
            else templates.SystemText.LIMIT_PARTIAL
        )
        assert notice.value in [c["text"] for c in claims], which
        assert [c.type for c in fd.message.cards] == ["GOAL_SAVED"], which  # type: ignore[union-attr]


def test_resume_removed_source_under_front_stops_even_when_a_limit_was_hit() -> None:
    """한도에 걸려도 지워진 원천 처리가 먼저다: 앞 문장이 기대면 한도 안내 대신 삭제 중단 문장으로 끝낸다."""
    for which in _limit_cases():
        req2, data, llm2 = _resume_at_limit(which)
        said = payload_of(data["state"])["loop"]["emitted"]
        req2["context"]["removed_sources"] = ["t-5"]
        _, _, d2 = F.run_turn(req2, llm2)
        fd = final_ok(d2)
        assert llm2.calls == [], which
        claims = [c.model_dump(mode="json") for c in fd.message.claims]  # type: ignore[union-attr]
        assert claims[: len(said)] == said, which
        assert claims[-1]["text"] == templates.SystemText.SOURCE_DELETED_STOP.value, which
        assert all(c["kind"] != "ACTION_RESULT" for c in claims), which


def test_end_without_model_with_only_the_front_is_partial_not_agent_timeout() -> None:
    """저장된 앞 문장이 있으면, 결과 문장 · 근거가 없어도 AGENT_TIMEOUT 으로 버리지 않고 PARTIAL 로 끝낸다(앞 문장은 `_finish`가 싣는다)."""
    from types import SimpleNamespace

    from chat_agent import turn as turn_mod

    captured: dict[str, Any] = {}

    def finish(**kw: Any) -> None:
        captured.update(kw)

    lp = SimpleNamespace(
        st=SimpleNamespace(emitted=[{"kind": "SYSTEM", "text": "앞 문장"}], executed=[]),
        ctx=SimpleNamespace(registry=SimpleNamespace(items={})),
    )
    turn_mod._end_without_model(lp, finish, "TIME")  # type: ignore[arg-type]
    assert captured["status"] == "PARTIAL" and "error" not in captured
    # 앞 문장도 결과도 근거도 없으면 지금처럼 AGENT_TIMEOUT
    captured.clear()
    lp.st.emitted = []
    turn_mod._end_without_model(lp, finish, "TIME")  # type: ignore[arg-type]
    assert captured["error"] == "AGENT_TIMEOUT"


# ── 상태 크기 초과 (5-6) ─────────────────────────────────────────────────────

GUIDE_CLAIM = {
    "kind": "GUIDE",
    "assessment_id": None,
    "text": "자료에 따르면 청중과 눈을 맞추며 말하는 연습이 도움이 돼요.",
    "evidence_ids": [],
    "source_ids": ["g-07#2"],
    "action_id": None,
}
GUIDE_CARD = {
    "source_id": "g-07#2",
    "title": "시선 처리",
    "quote": "청중과 눈을 맞추며 말하세요",
    "source": "교재",
    "license": "CC BY",
}


def with_guide_front(data: dict[str, Any]) -> dict[str, Any]:
    """첫 멈춤의 서명 상태에 자료를 인용한 앞 문장 + 그 자료 카드 사본을 더해 다시 서명한다."""
    from chat_agent import state as st

    p = payload_of(data["state"])
    p["loop"]["emitted"] = [*p["loop"]["emitted"], GUIDE_CLAIM]
    p["loop"]["emitted_sources"] = [GUIDE_CARD]
    return {**data, "state": st.sign(st.StatePayload.model_validate(p), F.SECRET)}


def test_state_too_large_keeps_the_front_sentences_and_their_source_cards(monkeypatch: Any) -> None:
    """둘째 승인 대기 상태가 한도를 넘으면 행동 없이 PARTIAL 로 끝내되, 앞 문장이 쓰는 자료 카드 사본은 버리지 않는다."""
    from chat_agent import state as st
    from chat_agent import turn as turn_mod

    req, _, data = pause(req=first_request(GOAL_AND_REASSESS_MSG))
    data = with_guide_front(data)
    said = payload_of(data["state"])["loop"]["emitted"]
    req2 = resume_request(req, data, saved_result(data["actions"][0]))

    def too_large(*a: Any, **k: Any) -> Any:
        raise st.StateTooLarge("크기 초과")

    monkeypatch.setattr(turn_mod.state, "sign", too_large)
    llm2 = F.FakeLlm([F.tools(REASSESS, prefix="ra")], answers=[F.answer([])], fixes=[F.fix({})])
    _, _, d2 = F.run_turn(req2, llm2)
    fd = final_ok(d2)
    assert fd.status == "PARTIAL" and fd.actions == [] and fd.state is None
    assert fd.message is not None
    claims = [c.model_dump(mode="json") for c in fd.message.claims]
    assert claims[: len(said)] == said
    assert templates.SystemText.STATE_TOO_LARGE.value in fd.message.text
    # 앞 문장의 자료 id 마다 카드 사본이 있다
    assert [g.model_dump(mode="json") for g in fd.message.sources] == [GUIDE_CARD]


def test_state_too_large_keeps_the_executed_result_sentence_and_card(monkeypatch: Any) -> None:
    """둘째 승인 상태가 크기 한도를 넘어 새 승인 행동을 버려도, 이미 실행 · 검증된 저장 결과 문장과 GOAL_SAVED 카드는 남는다."""
    from chat_agent import state as st
    from chat_agent import turn as turn_mod

    req, _, data = pause(req=first_request(GOAL_AND_REASSESS_MSG))
    said = data["message"]["claims"]
    req2 = resume_request(req, data, saved_result(data["actions"][0]))

    def too_large(*a: Any, **k: Any) -> Any:
        raise st.StateTooLarge("크기 초과")

    monkeypatch.setattr(turn_mod.state, "sign", too_large)
    llm2 = F.FakeLlm([F.tools(REASSESS, prefix="ra")], answers=[F.answer([])], fixes=[F.fix({})])
    _, _, d2 = F.run_turn(req2, llm2)
    fd = final_ok(d2)
    assert fd.status == "PARTIAL" and fd.actions == [] and fd.state is None
    claims = [c.model_dump(mode="json") for c in fd.message.claims]  # type: ignore[union-attr]
    assert claims[: len(said)] == said
    assert claims[len(said)]["kind"] == "ACTION_RESULT"
    assert claims[len(said)]["text"] == templates.ACTION_SUCCESS["SAVE_GOAL"]
    assert claims[-1]["text"] == templates.SystemText.STATE_TOO_LARGE.value
    assert [c.type for c in fd.message.cards] == ["GOAL_SAVED"]  # type: ignore[union-attr]


# ── 대기 중 원천이 지워짐 (5-5 · 5-6 · 12-2) ──────────────────────────────────


def test_removed_source_under_a_front_sentence_stops_the_resume_and_keeps_the_front() -> None:
    """이미 낸 문장이 기댄 원천이 지워지면 그 문장만 빼고 이어 가지 않는다: 모델 없이 낸 문장 그대로 PARTIAL + 고정 문장, 새 상태 없음."""
    req, _, data = pause()
    said = data["message"]["claims"]
    assert any(c["evidence_ids"] for c in said)
    req2 = resume_request(req, data, saved_result(data["actions"][0]))
    req2["context"]["removed_sources"] = ["t-5"]
    llm2 = F.FakeLlm([])
    _, _, d2 = F.run_turn(req2, llm2)
    fd = final_ok(d2)
    assert llm2.calls == []  # 지운 원천의 값은 모델에 다시 주지 않는다
    assert fd.status == "PARTIAL" and fd.actions == [] and fd.state is None
    claims = [c.model_dump(mode="json") for c in fd.message.claims]  # type: ignore[union-attr]
    assert claims[: len(said)] == said  # 앞부분은 이미 낸 문장 그대로
    assert claims[-1]["text"] == templates.SystemText.SOURCE_DELETED_STOP.value
    assert claims[-1]["kind"] == "SYSTEM"


# ── 측정값 이의 · 대본 진술(6-6 · L6-06, 4회차) ───────────────────────────────

DISPUTE_MSG = "Take 5는 카메라가 낮아서 시선이 낮게 나온 거야. 다시 평가해 줘"


def dispute_drafts() -> list[dict[str, Any]]:
    return [
        F.draft(
            "Take 5는 카메라가 낮아서 시선이 낮게 나온 거야",
            claim_op="ISSUE",
            dimension="audience_gaze",
            take_numbers=[5],
            polarity="NEGATIVE",
        ),
        F.draft("다시 평가해 줘", type_="REQUEST", claim_op=None, polarity="NEUTRAL"),
    ]


def test_measurement_dispute_gets_the_measurement_notice_without_reassess() -> None:
    """측정값 이의로 다시 평가를 바라면 코드가 "이 값은 측정 결과라 다시 평가해도 바뀌지 않아요"를 붙인다(6-6)."""
    llm = F.FakeLlm(
        [F.plan(assessments=dispute_drafts()), F.done()],
        answers=[F.answer([])],
        fixes=[F.fix({})],
    )
    _, _, data = F.run_turn(F.request(user_message=DISPUTE_MSG), llm)
    fd = final_ok(data)
    assert fd.actions == []
    assert templates.SystemText.MEASUREMENT_FIXED.value in fd.message.text  # type: ignore[union-attr]


def test_reassess_on_a_measurement_dispute_is_a_tool_error_and_gets_the_notice() -> None:
    """측정값 이의 말(대본 · 원고 · 기준 없음)에서 모델이 다시 평가를 내면 도구 오류로 돌려주고 확인 카드를 만들지 않는다."""
    llm = F.FakeLlm(
        [F.plan(assessments=dispute_drafts()), F.tools(REASSESS, prefix="ra"), F.done()],
        answers=[F.answer([])],
        fixes=[F.fix({})],
    )
    _, _, data = F.run_turn(F.request(user_message=DISPUTE_MSG), llm)
    fd = final_ok(data)
    assert fd.status != "NEEDS_ACTION" and fd.actions == []
    assert "CONDITION_NOT_STATED" in json.dumps(llm.calls[2].messages, ensure_ascii=False)
    assert templates.SystemText.MEASUREMENT_FIXED.value in fd.message.text  # type: ignore[union-attr]


def test_script_statement_typed_as_request_still_gets_the_screen_script_fact() -> None:
    """'대본 보고 한 발표였어'를 모델이 요청(REQUEST)으로 분류해도 코드가 대본 주장으로 바로잡아 화면 대본 사실 문장을
    붙인다(L6-06, 4회차 ra-script). 조건을 말한 다시 평가라 측정값 안내는 붙이지 않는다."""
    msg = "Take 5는 대본 보고 한 발표였어. 그 조건으로 다시 평가해 줘"
    d = [
        F.draft(
            "Take 5는 대본 보고 한 발표였어",
            type_="REQUEST",
            claim_op=None,
            dimension="script_independence",
            take_numbers=[5],
            polarity="NEUTRAL",
        ),
        F.draft("그 조건으로 다시 평가해 줘", type_="REQUEST", claim_op=None, polarity="NEUTRAL"),
    ]
    llm = F.FakeLlm(
        [F.plan(assessments=d), F.tools(REASSESS, prefix="ra")],
        answers=[F.answer([F.claim("ASSESSMENT", "", assessment_index=0)])],
        fixes=[F.fix({})],
    )
    _, _, data = F.run_turn(F.request(user_message=msg), llm)
    fd = final_ok(data)
    assert [a.type for a in fd.actions] == ["REASSESS"]
    text = fd.message.text  # type: ignore[union-attr]
    assert "기록에는 화면 대본이" in text and templates.SCRIPT_UNKNOWN in text
    assert templates.SystemText.MEASUREMENT_FIXED.value not in text


def test_measurement_notice_also_for_asking_to_look_again() -> None:
    """'다시 봐 줘'도 다시 평가 요청이다(측정값 이의면 안내를 붙인다)."""
    msg = "시선 잘 봤는데 왜 낮게 나와? 다시 봐 줘"
    d = [
        F.draft(
            "시선 잘 봤는데 왜 낮게 나와",
            claim_op="OVERALL",
            dimension="audience_gaze",
            polarity="POSITIVE",
        ),
        F.draft("다시 봐 줘", type_="REQUEST", claim_op=None, polarity="NEUTRAL"),
    ]
    llm = F.FakeLlm([F.plan(assessments=d), F.done()], answers=[F.answer([])], fixes=[F.fix({})])
    _, _, data = F.run_turn(F.request(user_message=msg), llm)
    assert templates.SystemText.MEASUREMENT_FIXED.value in final_ok(data).message.text  # type: ignore[union-attr]


def test_saying_it_stays_low_even_looking_again_is_not_a_reassess_request() -> None:
    """'다시 봐도 시선이 낮아'는 서술이다(다시 평가 요청이 아니면 측정값 안내를 붙이지 않는다)."""
    msg = "Take 5 다시 봐도 시선이 낮아"
    d = [F.draft(msg, claim_op="OVERALL", dimension="audience_gaze", take_numbers=[5])]
    llm = F.FakeLlm([F.plan(assessments=d), F.done()], answers=[F.answer([])], fixes=[F.fix({})])
    _, _, data = F.run_turn(F.request(user_message=msg), llm)
    assert templates.SystemText.MEASUREMENT_FIXED.value not in final_ok(data).message.text  # type: ignore[union-attr]
