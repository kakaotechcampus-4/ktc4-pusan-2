"""조사 흐름 시험 — 계획의 판단 초안이 집은 기록을 모델이 고르기 전에 코드가 먼저 읽는다(기능 문서 6-1 2번 · 6-3 · L2-22).

- 대상 Take 가 기본 자료에 없으면 모델 조사 호출 없이 `fetch_take`(AUTO)로 멈추고, 이어 부르면 그 Take 를 먼저 읽는다.
- 대상이 있으면 그 장 · 지표를 읽은 결과를 모델 조사 호출의 앞 도구 결과로 준다. 모델이 같은 것을 한 번 더 읽어도
  되풀이(`REPEAT`)로 끝나지 않는다.
- 말한 Take 가 하나라도 이 피치에 없으면 읽지 않는다(다른 Take 로 바꿔 읽지 않는다). 기준선(13-3) 설정은 끈다.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from pitch_coach_ai.features.chat_agent.config import load_config
from pitch_coach_ai.features.chat_agent.schemas import FinalData

from . import fakes as F
from .test_turn import fetched_take, resume_request

pytestmark = pytest.mark.prefetch

MSG = "Take 4 3장 청중 시선 몇 %였어?"


def slide_draft(take_numbers: list[int]) -> dict[str, Any]:
    return F.draft(
        "Take 4 3장 청중 시선 몇 %였어",
        type_="REQUEST",
        claim_op=None,
        dimension="audience_gaze",
        metric="audience_ratio",
        slide_number=3,
        take_numbers=take_numbers,
        polarity="NEUTRAL",
    )


def value_answer(r: Any) -> Any:
    eid = F.find_evidence(r, lambda o: o.get("metric") == "audience_ratio")
    return F.answer(
        [F.claim("RECORD", "Take 4의 3장 청중 시선 비율은 31%예요.", evidence_ids=[eid])]
    )


def tool_calls_in(req: Any) -> list[tuple[str, dict[str, Any]]]:
    out = []
    for m in req.messages:
        for tc in m.get("tool_calls") or []:
            out.append((tc["function"]["name"], json.loads(tc["function"]["arguments"])))
    return out


def test_missing_target_take_is_fetched_before_any_research_call() -> None:
    req = F.request(user_message=MSG)
    llm = F.FakeLlm([F.plan(assessments=[slide_draft([4])])])
    _, _, data = F.run_turn(req, llm)
    fd = FinalData.model_validate(data)
    assert fd.status == "NEEDS_ACTION"
    assert [(a.type, a.args["take_id"]) for a in fd.actions] == [("FETCH_TAKE", "t-4")]
    assert [c.purpose for c in llm.calls] == [
        "RESEARCH"
    ]  # 계획 한 번뿐: 조사 모델 호출 없이 가져오기를 냈다

    res = [
        {
            "action_id": fd.actions[0].action_id,
            "type": "FETCH_TAKE",
            "status": "SUCCEEDED",
            "data": {"take": fetched_take("t-4")},
            "error": None,
        }
    ]
    llm2 = F.FakeLlm([F.done(), value_answer])
    _, _, data2 = F.run_turn(resume_request(req, data, res), llm2)
    fd2 = FinalData.model_validate(data2)
    assert fd2.status == "DONE"
    # 이어 부른 뒤 첫 조사 호출 앞에 코드가 읽은 Take 4 3장 값이 있다
    first = llm2.calls[0]
    assert (
        "get_take_report",
        {"take_id": "t-4", "section": "slides", "slide_number": 3},
    ) in tool_calls_in(first)
    assert "31%" in fd2.message.text  # type: ignore[union-attr]


def test_loaded_target_is_read_first_and_a_model_repeat_is_not_a_stop() -> None:
    req = F.with_loaded(F.request(user_message=MSG), "t-4")
    same = ("get_take_report", {"take_id": "t-4", "section": "slides", "slide_number": 3})
    llm = F.FakeLlm([F.plan(assessments=[slide_draft([4])]), F.tools(same), F.done(), value_answer])
    _, _, data = F.run_turn(req, llm)
    fd = FinalData.model_validate(data)
    assert same in tool_calls_in(llm.calls[1])  # 모델의 첫 조사 호출 전에 이미 읽었다
    # 모델이 같은 것을 한 번 더 읽어도(캐시) 되풀이로 끝내지 않는다
    assert fd.status == "DONE" and fd.diagnostics.end.reason == "ANSWERED"  # type: ignore[union-attr]


def test_unknown_named_take_is_not_replaced_by_another_take() -> None:
    req = F.with_loaded(F.request(user_message=MSG), "t-4")
    llm = F.FakeLlm(
        [F.plan(assessments=[slide_draft([4, 9])]), F.done(), F.answer([])],
    )
    F.run_turn(req, llm)
    # 말한 Take 9 가 없으니 코드는 아무것도 읽지 않는다(Take 4 만 골라 읽지 않음, L2-22)
    assert tool_calls_in(llm.calls[1]) == []


def test_change_claim_without_named_takes_compares_with_the_previous_take() -> None:
    d = F.draft(
        "지난번보다 3장 시선 좋아졌지",
        claim_op="CHANGE",
        dimension="audience_gaze",
        slide_number=3,
        polarity="POSITIVE",
    )
    req = F.with_loaded(F.request(user_message="지난번보다 3장 시선 좋아졌지?"), "t-4")
    llm = F.FakeLlm([F.plan(assessments=[d]), F.done(), F.answer([])])
    F.run_turn(req, llm)
    calls = tool_calls_in(llm.calls[1])
    assert calls and calls[0][0] == "compare_takes"
    assert (calls[0][1]["previous_take_id"], calls[0][1]["current_take_id"]) == ("t-4", "t-5")
    assert calls[0][1]["metric"] == "audience_ratio"  # 차원의 첫 지표


def test_baseline_config_turns_prefetch_off() -> None:
    req = F.with_loaded(F.request(user_message=MSG), "t-4")
    llm = F.FakeLlm([F.plan(assessments=[slide_draft([4])]), F.done(), F.answer([])])
    F.run_turn(req, llm, cfg=load_config(research_prefetch=False))
    assert tool_calls_in(llm.calls[1]) == []


def test_prefetch_reads_stop_at_the_tool_limit() -> None:
    """조사 흐름 중 한도: 코드가 먼저 읽는 것도 도구 한도(8-2)에 들어간다. 읽을 장 셋 중 한도(2번)까지만 읽고, 모델 조사
    호출 없이 그때까지 읽은 근거로 답을 써 `TOOL_CALLS`로 끝낸다(한도 뒤 요청은 실행하지 않음)."""

    def slide1_answer(r: Any) -> Any:
        # 마지막으로 읽은 결과(1장)의 청중 시선 근거. 그 뒤 세 번째 읽기는 LIMIT 도구 오류다
        eid = F.find_evidence(r, lambda o: o.get("metric") == "audience_ratio")
        return F.answer(
            [F.claim("RECORD", "Take 5의 1장 청중 시선 비율은 62%예요.", evidence_ids=[eid])]
        )

    drafts = [
        F.draft(
            f"{n}장 시선",
            type_="REQUEST",
            claim_op=None,
            dimension="audience_gaze",
            metric="audience_ratio",
            slide_number=n,
            take_numbers=[5],
            polarity="NEUTRAL",
        )
        for n in (3, 1, 2)
    ]
    req = F.request(user_message="3장 시선, 1장 시선, 2장 시선 몇 %였어?")
    llm = F.FakeLlm([F.plan(assessments=drafts)], answers=[slide1_answer], fixes=[F.fix({})])
    _, _, data = F.run_turn(req, llm, cfg=load_config(max_tool_calls=2))
    fd = FinalData.model_validate(data)
    assert fd.diagnostics is not None and fd.diagnostics.end.reason == "TOOL_CALLS"
    assert fd.status == "PARTIAL" and fd.actions == []
    assert len(fd.trace) == 2  # 실행한 읽기는 둘
    # 답 쓰기에 간 결과는 호출 순서 그대로다. 코드가 낸 요청은 초안 순서(3 · 1 · 2장) 셋뿐이므로, Take 5 3장 성공 →
    # 1장 성공 → 세 번째(2장)는 실행하지 않고 LIMIT 도구 오류
    seen = [
        (True, r["data"]["take_id"], r["data"]["items"][0]["slide_number"])
        if r["ok"]
        else (False, r["error"]["code"], None)
        for r in F.tool_results(llm.calls[-1])
    ]
    assert seen == [(True, "t-5", 3), (True, "t-5", 1), (False, "LIMIT", None)]
    assert "62%" in fd.message.text  # type: ignore[union-attr]
    assert [c.purpose for c in llm.calls].count("RESEARCH") == 1  # 계획 뒤 모델 조사 호출 없음


def _then_request(basis_takes: list[tuple[str, int]]) -> dict[str, Any]:
    """옛 대화(앞 답이 Take 4 를 근거로 함)를 연 뒤 '그때 3장 어땠지?'(6-8 '그때(Take n) 기준')."""
    req = F.request(user_message="그때 3장 어땠지?")
    req["history"]["messages"] = [
        {"role": "user", "text": "Take 4 3장 시선 어땠어?"},
        {
            "role": "assistant",
            "text": "Take 4의 3장 청중 시선 비율은 31%예요.",
            "basis": {
                "pitches": [
                    {
                        "pitch_id": "p-1",
                        "takes": [{"take_id": t, "take_number": n} for t, n in basis_takes],
                    }
                ]
            },
        },
    ]
    return req


def _then_draft() -> dict[str, Any]:
    return F.draft(
        "그때 3장 어땠지",
        type_="REQUEST",
        claim_op=None,
        dimension="audience_gaze",
        metric="audience_ratio",
        slide_number=3,
        polarity="NEUTRAL",
    )


def test_then_word_fills_the_take_from_the_last_answer_basis() -> None:
    """'그때'처럼 앞 답을 가리키는 말: 초안에 Take 가 없으면 가장 최근 assistant 메시지 근거 표시의 Take 하나(Take 4)로 채워
    기본 자료에 없는 그 Take 를 먼저 가져오고, 이어 부르면 그 Take 의 3장을 읽는다(지금 보던 Take 5 를 읽지 않는다)."""
    req = _then_request([("t-4", 4)])
    llm = F.FakeLlm([F.plan(assessments=[_then_draft()])])
    _, _, data = F.run_turn(req, llm)
    fd = FinalData.model_validate(data)
    assert fd.status == "NEEDS_ACTION"
    assert [(a.type, a.args["take_id"]) for a in fd.actions] == [("FETCH_TAKE", "t-4")]

    res = [
        {
            "action_id": fd.actions[0].action_id,
            "type": "FETCH_TAKE",
            "status": "SUCCEEDED",
            "data": {"take": fetched_take("t-4")},
            "error": None,
        }
    ]
    llm2 = F.FakeLlm([F.done(), value_answer])
    _, _, data2 = F.run_turn(resume_request(req, data, res), llm2)
    fd2 = FinalData.model_validate(data2)
    assert fd2.status == "DONE"
    assert (
        "get_take_report",
        {"take_id": "t-4", "section": "slides", "slide_number": 3},
    ) in tool_calls_in(llm2.calls[0])
    assert "31%" in fd2.message.text  # type: ignore[union-attr]


def test_then_word_with_two_takes_in_the_basis_does_not_guess() -> None:
    """앞 답의 근거 표시가 Take 둘을 가리키면 어느 쪽인지 정하지 않는다: 초안을 채우지 않아 판정 대상 Take(지금 보던 Take 5)를 읽는다."""
    req = F.with_loaded(_then_request([("t-4", 4), ("t-3", 3)]), "t-5")
    llm = F.FakeLlm([F.plan(assessments=[_then_draft()]), F.done(), F.answer([])])
    _, _, data = F.run_turn(req, llm)
    fd = FinalData.model_validate(data)
    assert all(a.type != "FETCH_TAKE" for a in fd.actions)
    assert (
        "get_take_report",
        {"take_id": "t-5", "section": "slides", "slide_number": 3},
    ) in tool_calls_in(llm.calls[1])


@pytest.mark.parametrize("guard", [True, False])
def test_fetch_result_alone_is_not_an_answer_but_the_target_record_is(guard: bool) -> None:
    """가져오기 결과 문장만 남은 답(모델 문장이 모두 빠짐)은 그것만으로 DONE 이 아니다(끝 상태는 요청 해소로, 6-9 '고친 뒤'). 질문 대상(Take 4
    3장 청중 시선)의 읽은 기록으로 그린 코드 문장이 있으면 그 문장이 답이고(DONE), 그릴 대상 문장이 없으면 부분 답(PARTIAL)이다."""
    cfg = load_config(record_answer_guard=guard)
    req = F.request(user_message=MSG)
    _, _, d1 = F.run_turn(req, F.FakeLlm([F.plan(assessments=[slide_draft([4])])]), cfg=cfg)
    action = d1["actions"][0]
    res = [
        {
            "action_id": action["action_id"],
            "type": "FETCH_TAKE",
            "status": "SUCCEEDED",
            "data": {"take": fetched_take("t-4")},
            "error": None,
        }
    ]
    bad = "Take 4의 3장 청중 시선 비율은 99%예요."  # 근거 없는 숫자: 검사로 빠진다
    llm2 = F.FakeLlm(
        [F.done()],
        answers=[F.answer([F.claim("RECORD", bad, evidence_ids=[])])],
        fixes=[F.fix({0: None})],
    )
    _, _, d2 = F.run_turn(resume_request(req, d1, res), llm2, cfg=cfg)
    fd = FinalData.model_validate(d2)
    said = [c.text for c in fd.message.claims]  # type: ignore[union-attr]
    assert said[0] == "Take 4 기록을 가져왔어요." and bad not in said
    if guard:
        assert any("31%" in t for t in said[1:])  # 대상 근거로 그린 기록 문장(기록 답 보장)
        assert fd.status == "DONE"
    else:
        assert fd.status == "PARTIAL"  # 가져오기 결과 문장뿐: 질문이 풀리지 않았다
