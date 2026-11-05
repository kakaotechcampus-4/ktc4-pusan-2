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

from chat_agent.config import load_config
from chat_agent.schemas import FinalData

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
    return F.answer([F.claim("RECORD", "Take 4의 3장 청중 시선 비율은 31%예요.", evidence_ids=[eid])])


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
    assert [c.purpose for c in llm.calls] == ["RESEARCH"]  # 계획 한 번뿐: 조사 모델 호출 없이 가져오기를 냈다

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
    assert ("get_take_report", {"take_id": "t-4", "section": "slides", "slide_number": 3}) in tool_calls_in(first)
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
