"""한도 · 멈춤 조건 시험 — 모델 호출 18번 · 출력 정산 · 응답마다의 답 몫 · 교대 반복 · 끝난 이유(`end.reason`) (기능 문서 8-2 · 8-3 · 5-7 · 11-5).

실제 모델 없이 가짜 모델(tests/unit/fakes.py)로 `/agent/turn` 흐름을 돌린다. 이름의 L 번호는 기능 문서 7절 실패 행이다.
"""

from __future__ import annotations

import base64
import json
from typing import Any

import pytest

from chat_agent import state as st
from chat_agent.config import load_config
from chat_agent.llm_types import LlmTimeout, Usage
from chat_agent.loop import _alternating
from chat_agent.schemas import FinalData

from . import fakes as F
from .test_turn import (
    COMPARE,
    MSG,
    basic_script,
    change_draft,
    compare_answer,
    fetch_take_script,
    fetched_take,
    final_ok,
    resume_request,
)

REPORT = "get_take_report"


def read(take: str, slide: int) -> tuple[str, dict[str, Any]]:
    return (REPORT, {"take_id": take, "section": "slides", "slide_number": slide})


def invalid() -> Any:
    """필수 인자가 빠진 무효 도구 호출(예약되는 도구 호출이 아니고 반복 키에도 들지 않는다)."""
    return F.tools((REPORT, {"take_id": "t-4"}), prefix="bad")


def reason_of(fd: FinalData) -> str:
    assert fd.diagnostics is not None
    return fd.diagnostics.end.reason


def purposes(llm: F.FakeLlm) -> list[str]:
    return [c.purpose for c in llm.calls]


def request() -> dict[str, Any]:
    return F.with_loaded(F.request(user_message=MSG), "t-4")


# ── 호출 수 18번 · 조사 16번 (L1-13) ────────────────────────────────────────


def test_l1_13_research_attempts_stop_at_sixteen_and_answer_still_runs() -> None:
    """유효 읽기와 무효 응답을 번갈아 조사 시도를 길게 이으면 16번째에서 멈추고, 답 쓰기 몫이 남는다."""
    loop: list[Any] = []
    reads = [read(t, n) for t in ("t-5", "t-4") for n in (1, 2, 3, 4)]
    for i, r in enumerate(reads):
        loop += [F.tools(r, prefix=f"l{i}"), invalid()]
    llm = F.FakeLlm(
        [F.plan(assessments=[change_draft()]), *loop],
        answers=[F.answer([F.claim("ASSESSMENT", "", assessment_index=0)])],
    )
    # 입력 추정량(한 번에 수천 토큰)이 72k 에 먼저 닿지 않게 입력 한도만 풀어 호출 수 한도를 본다
    _, _, data = F.run_turn(request(), llm, cfg=load_config(max_input_tokens=10_000_000))
    fd = final_ok(data)
    p = purposes(llm)
    assert p.count("RESEARCH") == 16  # 계획 1 + 조사 15, 이번 시도를 포함해 16을 넘기지 않는다
    assert p[-1] == "ANSWER"
    assert fd.budget_used.model_calls == 17 <= 18
    assert fd.status == "PARTIAL" and reason_of(fd) == "MODEL_CALLS"


def test_l1_13_default_values_allow_eighteen_attempts_in_one_run() -> None:
    """17 · 18번째 시도는 답 쓰기 · 고치기 몫이다(조사는 16번째까지)."""
    cfg = load_config()
    assert (cfg.max_model_calls, cfg.reserved_answer_calls) == (18, 2)
    assert cfg.max_model_calls - cfg.reserved_answer_calls == 16


# ── 출력 정산 (8-2 · 8-3 · L1-39) ───────────────────────────────────────────


def test_l1_39_output_is_settled_to_measured_usage_per_response() -> None:
    """모델 응답마다 출력 예약(2,000)을 실측(50)으로 바꾼다: 4번 호출 = 200."""
    llm = F.FakeLlm(basic_script(), usage=Usage(100, 50))
    fd = final_ok(F.run_turn(request(), llm)[2])
    assert fd.budget_used.model_calls == 4
    assert fd.budget_used.output_tokens == 4 * 50
    assert fd.usage.output_tokens == 4 * 50  # 관측 사용량과 같은 값(5-5)
    assert fd.budget_used.input_tokens > 0  # 입력은 전송 전 예약 누적


def test_l1_39_unknown_usage_keeps_the_whole_reservation() -> None:
    """공급자가 출력 사용량을 주지 않으면 예약 전체(호출당 2,000)를 쓴 것으로 둔다."""
    llm = F.FakeLlm(basic_script(), usage=Usage(100, None))
    fd = final_ok(F.run_turn(request(), llm)[2])
    assert fd.budget_used.output_tokens == fd.budget_used.model_calls * 2_000
    assert fd.usage.output_tokens is None and fd.usage.measured is False


def test_l1_39_failed_attempt_keeps_its_reservation() -> None:
    """실패한 시도는 사용량을 몰라 예약 그대로(2,000)이고, 성공한 시도만 실측으로 정산한다."""
    llm = F.FakeLlm(
        [F.plan(assessments=[change_draft()]), LlmTimeout()],
        answers=[F.answer([])],
        usage=Usage(100, 50),
    )
    fd = final_ok(F.run_turn(request(), llm)[2])
    assert purposes(llm) == ["RESEARCH", "RESEARCH", "ANSWER"]
    # 계획 50 + 실패한 조사 시도 2,000(예약 그대로) + 답 쓰기 50
    assert fd.budget_used.output_tokens == 50 + 2_000 + 50


def test_l1_39_settlement_lets_a_long_run_pass_the_8k_cap() -> None:
    """예약만으로는 4번 호출이면 8k 에 닿지만, 실측 정산은 더 긴 조사를 허용한다(출력 누적 8k)."""
    loop: list[Any] = []
    for i, r in enumerate([read(t, n) for t in ("t-5", "t-4") for n in (1, 2, 3, 4)]):
        loop.append(F.tools(r, prefix=f"l{i}"))
    llm = F.FakeLlm(
        [F.plan(assessments=[change_draft()]), *loop, F.done()],
        answers=[F.answer([F.claim("ASSESSMENT", "", assessment_index=0)])],
        usage=Usage(100, 40),
    )
    fd = final_ok(F.run_turn(request(), llm, cfg=load_config(max_input_tokens=10_000_000))[2])
    assert fd.budget_used.model_calls > 4
    assert reason_of(fd) != "TOKENS"
    assert fd.budget_used.output_tokens == fd.budget_used.model_calls * 40


def test_l1_39_output_cap_ends_research_with_tokens_reason() -> None:
    """정산해도 누적 출력이 상한에 닿으면 더 부르지 않는다."""
    reads = [F.tools(read(t, n), prefix=f"l{t}{n}") for t in ("t-5", "t-4") for n in (1, 2, 3, 4)]
    llm = F.FakeLlm(
        [F.plan(assessments=[change_draft()]), *reads],
        answers=[F.answer([F.claim("ASSESSMENT", "", assessment_index=0)])],
        usage=Usage(100, 1_500),
    )
    fd = final_ok(F.run_turn(request(), llm)[2])
    assert fd.status == "PARTIAL" and reason_of(fd) == "TOKENS"
    assert fd.budget_used.output_tokens <= 8_000


def test_l1_31_settlement_never_lowers_the_exported_budget_used() -> None:
    """이어 부르기: 응답의 `budget_used`가 서명 누적보다 작아지지 않아 그대로 다시 보내면 409 가 아니다."""
    req = F.request(user_message=MSG)
    llm = F.FakeLlm(fetch_take_script(), usage=Usage(100, 30))
    _, _, data = F.run_turn(req, llm)
    first = data["budget_used"]
    assert first["output_tokens"] == 3 * 30
    action = data["actions"][0]
    res = [
        {
            "action_id": action["action_id"],
            "type": "FETCH_TAKE",
            "status": "SUCCEEDED",
            "data": {"take": fetched_take("t-4")},
            "error": None,
        }
    ]
    llm2 = F.FakeLlm(
        [F.tools(COMPARE, prefix="c2"), F.done(), compare_answer()], usage=Usage(100, 5)
    )
    status, _, data2 = F.run_turn(resume_request(req, data, res), llm2)
    assert status == 200
    second = final_ok(data2).budget_used
    assert second.output_tokens == first["output_tokens"] + 3 * 5
    assert second.output_tokens >= first["output_tokens"]


# ── 응답마다 답 쓰기 · 고치기 몫 (8-3) ──────────────────────────────────────


def resume_with_calls(model_calls: int) -> tuple[dict[str, Any], F.FakeLlm]:
    """서명 상태의 모델 호출 누적을 바꾼 이어 부르기 요청(요청 `budget_used`도 같게 맞춘다)."""
    req = F.request(user_message=MSG)
    _, _, data = F.run_turn(req, F.FakeLlm(fetch_take_script()))
    payload = json.loads(base64.urlsafe_b64decode(data["state"]["payload"] + "=="))
    payload["loop"]["counters"]["model_calls"] = model_calls
    signed = st.sign(st.StatePayload.model_validate(payload), F.SECRET)
    action = data["actions"][0]
    res = [
        {
            "action_id": action["action_id"],
            "type": "FETCH_TAKE",
            "status": "SUCCEEDED",
            "data": {"take": fetched_take("t-4")},
            "error": None,
        }
    ]
    req2 = resume_request(req, {**data, "state": signed}, res)
    req2["budget_used"]["model_calls"] = model_calls
    llm2 = F.FakeLlm(
        [F.tools(COMPARE, prefix="c2"), F.done(), compare_answer()],
        answers=[F.answer([F.claim("ASSESSMENT", "", assessment_index=0)])],
    )
    return req2, llm2


@pytest.mark.parametrize("calls", [17, 18])
def test_l1_13_resume_without_room_for_two_answer_slots_ends_without_model(calls: int) -> None:
    """이어 부를 때 남은 호출로 답 쓰기 · 고치기 두 몫을 남길 수 없으면 모델 없이 검증된 결과로 끝낸다."""
    req2, llm2 = resume_with_calls(calls)
    _, _, data2 = F.run_turn(req2, llm2)
    assert llm2.calls == []
    # 검증된 근거가 아직 없어 템플릿 PARTIAL 도 만들 수 없으면 `AGENT_TIMEOUT`, 이유는 호출 수다
    assert data2["error"]["code"] == "AGENT_TIMEOUT"
    assert data2["diagnostics"]["end"]["reason"] == "MODEL_CALLS"


def test_l1_13_resume_with_two_slots_left_skips_research_and_answers() -> None:
    """누적 16: 조사(17번째 시도)는 막고 이 응답의 답 쓰기 몫으로 답한다."""
    req2, llm2 = resume_with_calls(16)
    _, _, data2 = F.run_turn(req2, llm2)
    assert purposes(llm2) == ["ANSWER"]  # 조사 시도 없이 답 쓰기만
    assert data2["diagnostics"]["end"]["reason"] == "MODEL_CALLS"
    if "budget_used" in data2:
        assert data2["budget_used"]["model_calls"] == 17 <= 18


def test_l1_13_answer_slots_are_per_response_not_per_run() -> None:
    """두 번째 응답에서도 조사 16번 · 답 몫이 새로 남는다: 누적 5 + 이어 부른 응답의 조사가 정상으로 돈다."""
    req2, llm2 = resume_with_calls(5)
    _, _, data2 = F.run_turn(req2, llm2)
    fd = final_ok(data2)
    assert purposes(llm2) == ["RESEARCH", "RESEARCH", "ANSWER"]
    assert fd.status == "DONE" and reason_of(fd) == "ANSWERED"
    assert fd.budget_used.model_calls == 8


# ── 교대 반복 (5-7 · L1-16) ─────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("keys", "alternating"),
    [
        (["A", "B", "A", "B"], True),
        (["x", "A", "B", "A", "B"], True),  # 최근 네 요청만 본다
        (["A", "B", "A", "C"], False),
        (["A", "B", "C", "B"], False),
        (["A", "A", "A", "A"], False),  # 같은 요청 반복은 기존 규칙(연속 2회)이 맡는다
        (["A", "B", "A"], False),
        ([], False),
    ],
)
def test_l1_16_alternating_window(keys: list[str], alternating: bool) -> None:
    assert _alternating(keys) is alternating


def reads_ab() -> tuple[tuple[str, dict[str, Any]], tuple[str, dict[str, Any]]]:
    return read("t-4", 1), read("t-4", 2)


def test_l1_16_alternating_requests_end_research_with_repeat_reason() -> None:
    """A-B-A-B: 셋째 · 넷째는 캐시 적중(새 근거 없음)이라 네 요청째에서 조사를 끝낸다."""
    a, b = reads_ab()
    llm = F.FakeLlm(
        [
            F.plan(assessments=[change_draft()]),
            F.tools(a, prefix="a1"),
            F.tools(b, prefix="b1"),
            F.tools(a, prefix="a2"),
            F.tools(b, prefix="b2"),
            F.tools(read("t-4", 3), prefix="never"),  # 쓰이면 안 된다
        ],
        answers=[F.answer([F.claim("ASSESSMENT", "", assessment_index=0)])],
    )
    fd = final_ok(F.run_turn(request(), llm)[2])
    assert purposes(llm) == ["RESEARCH"] * 5 + [
        "ANSWER"
    ]  # 계획 + 조사 4번(교대 반복 네 요청째) + 답
    assert fd.status == "PARTIAL" and reason_of(fd) == "REPEAT"


def test_l1_16_a_b_a_c_continues() -> None:
    a, b = reads_ab()
    c = read("t-4", 3)
    llm = F.FakeLlm(
        [
            F.plan(assessments=[change_draft()]),
            F.tools(a, prefix="a1"),
            F.tools(b, prefix="b1"),
            F.tools(a, prefix="a2"),
            F.tools(c, prefix="c1"),
            F.done(),
        ],
        answers=[F.answer([F.claim("ASSESSMENT", "", assessment_index=0)])],
    )
    fd = final_ok(F.run_turn(request(), llm)[2])
    assert purposes(llm) == ["RESEARCH"] * 6 + ["ANSWER"]
    assert reason_of(fd) != "REPEAT"


def test_l1_16_alternation_counts_cache_hits_inside_batched_responses() -> None:
    """한 응답에 A · B 를 함께 내고 다음 응답에서 되풀이해도(캐시 적중도 요청) 네 요청째에서 끝낸다."""
    a, b = reads_ab()
    llm = F.FakeLlm(
        [
            F.plan(assessments=[change_draft()]),
            F.tools(a, b, prefix="x1"),
            F.tools(a, b, prefix="x2"),
            F.tools(read("t-4", 3), prefix="never"),
        ],
        answers=[F.answer([F.claim("ASSESSMENT", "", assessment_index=0)])],
    )
    fd = final_ok(F.run_turn(request(), llm)[2])
    assert purposes(llm) == ["RESEARCH"] * 3 + ["ANSWER"]
    assert reason_of(fd) == "REPEAT"


# ── 끝난 이유 값 (11-5) ─────────────────────────────────────────────────────


def test_end_reason_time_when_turn_time_is_gone_at_start() -> None:
    req = request()
    req["budget_used"]["elapsed_ms"] = 120_000
    llm = F.FakeLlm([])
    fd_or_err = F.run_turn(req, llm)
    assert llm.calls == []
    _, _, data = fd_or_err
    if data.get("status"):
        assert final_ok(data).diagnostics.end.reason == "TIME"  # type: ignore[union-attr]
    else:
        assert data["error"]["code"] == "AGENT_TIMEOUT"
        assert data["diagnostics"]["end"]["reason"] == "TIME"


def test_end_reason_time_when_research_has_no_time_left() -> None:
    """남은 시간이 모자라 조사를 못 보내면 `TIME`(120초 · 호출 45초)."""
    clock = F.Clock()
    llm_calls: list[Any] = []

    def advance_then_plan(req: Any) -> Any:
        llm_calls.append(req)
        clock.t += 30  # 계획에 30초를 써서 조사 시간(남은 15초 − 답 몫 24초)이 없다
        return F.plan(assessments=[change_draft()])

    llm = F.FakeLlm(
        [advance_then_plan], answers=[F.answer([F.claim("ASSESSMENT", "", assessment_index=0)])]
    )
    fd = final_ok(F.run_turn(request(), llm, clock=clock)[2])
    assert fd.status == "PARTIAL" and reason_of(fd) == "TIME"


def test_end_reason_tool_calls_when_twelve_tool_runs_are_used() -> None:
    reads = [read(t, n) for t in ("t-5", "t-4") for n in (1, 2, 3, 4)] + [
        ("get_take_report", {"take_id": "t-4", "section": "pace"}),
        ("get_take_report", {"take_id": "t-4", "section": "content"}),
    ]
    script = [F.plan(assessments=[change_draft()])]
    script += [F.tools(r, prefix=f"r{i}") for i, r in enumerate(reads)]
    cfg = load_config(max_tool_calls=3)
    llm = F.FakeLlm(script, answers=[F.answer([F.claim("ASSESSMENT", "", assessment_index=0)])])
    fd = final_ok(F.run_turn(request(), llm, cfg=cfg)[2])
    assert fd.status == "PARTIAL" and reason_of(fd) == "TOOL_CALLS"


def test_end_reason_tokens_when_input_cap_is_reached() -> None:
    llm = F.FakeLlm(
        [F.plan(assessments=[change_draft()]), F.tools(COMPARE)],
        answers=[F.answer([F.claim("ASSESSMENT", "", assessment_index=0)])],
    )
    fd = final_ok(F.run_turn(request(), llm, cfg=load_config(max_input_tokens=9_000))[2])
    assert fd.status == "PARTIAL" and reason_of(fd) == "TOKENS"


def test_end_reason_repeat_when_same_request_is_repeated() -> None:
    llm = F.FakeLlm(
        [
            F.plan(assessments=[change_draft()]),
            F.tools(COMPARE, prefix="a"),
            F.tools(COMPARE, prefix="b"),
        ],
        answers=[compare_answer()],
    )
    fd = final_ok(F.run_turn(request(), llm)[2])
    assert fd.status == "PARTIAL" and reason_of(fd) == "REPEAT"


def test_end_reason_repeat_when_invalid_tool_responses_repeat() -> None:
    llm = F.FakeLlm(
        [F.plan(assessments=[change_draft()]), invalid(), invalid()],
        answers=[F.answer([F.claim("ASSESSMENT", "", assessment_index=0)])],
    )
    fd = final_ok(F.run_turn(request(), llm)[2])
    assert fd.status == "PARTIAL" and reason_of(fd) == "REPEAT"


def test_end_reason_check_all_dropped_when_every_sentence_fails_the_check() -> None:
    """답 검사에 모든 문장이 걸려 끝나면 한도 이유가 아니라 `CHECK_ALL_DROPPED`."""
    bad = F.answer(
        [F.claim("RECORD", "Take 5의 3장 청중 시선 비율은 99%예요.", evidence_ids=["e1"])]
    )
    llm = F.FakeLlm(
        [F.plan(), F.tools(COMPARE), F.done()],
        answers=[bad],
        fixes=[LlmTimeout()],
    )
    fd = final_ok(
        F.run_turn(F.with_loaded(F.request(user_message="Take 5 3장 기록 알려 줘"), "t-4"), llm)[2]
    )
    assert fd.status == "PARTIAL" and reason_of(fd) == "CHECK_ALL_DROPPED"


def test_end_reason_answered_stays_for_normal_runs() -> None:
    fd = final_ok(F.run_turn(request(), F.FakeLlm(basic_script()))[2])
    assert reason_of(fd) == "ANSWERED"
