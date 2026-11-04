"""`/agent/turn` 한 번의 흐름 시험 — 가짜 모델로 스트림 전 검사 · 진행 줄 · 행동 멈춤과 재개 · 한도 · 답 검사 · 자동 목표 확인을 본다.

실제 모델 없이 정해 둔 응답(tests/unit/fakes.py)을 쓴다. 이름의 L 번호는 기능 문서 7절 실패 행이다.
"""

from __future__ import annotations

import copy
import json
from typing import Any

import pytest

from chat_agent import templates
from chat_agent.config import load_config
from chat_agent.llm_types import LlmTimeout, Usage
from chat_agent.schemas import FinalData
from chat_agent.turn import handle_turn
from chat_agent_lab.fixtures import sample_pitch, take_snapshot

from . import fakes as F

MSG = "Take 4보다 Take 5에서 3장 시선이 좋아졌어?"
COMPARE = (
    "compare_takes",
    {
        "previous_take_id": "t-4",
        "current_take_id": "t-5",
        "metric": "audience_ratio",
        "slide_number": 3,
    },
)


def change_draft() -> dict[str, Any]:
    return F.draft(
        MSG.rstrip("?"),
        claim_op="CHANGE",
        dimension="audience_gaze",
        metric="audience_ratio",
        slide_number=3,
        take_numbers=[4, 5],
        polarity="POSITIVE",
    )


def roles(r: Any) -> dict[str, str]:
    for res in F.tool_results(r):
        d = res.get("data") or {}
        if "evidence_by_role" in d:
            return d["evidence_by_role"]
    raise AssertionError("compare_takes 결과가 없다")


def compare_answer(
    *extra: dict[str, Any], record_text: str = "Take 5의 3장 청중 시선 비율은 28%예요."
) -> Any:
    def make(r: Any) -> Any:
        ro = roles(r)
        claims = [
            F.claim("ASSESSMENT", "3장에서 청중을 보는 시간을 늘려 보세요.", assessment_index=0),
            F.claim("CONCLUSION", "", comparison_evidence_id=ro["outcome"]),
            F.claim("RECORD", record_text, evidence_ids=[ro["current"]]),
            *extra,
        ]
        return F.answer(claims, history_summary="3장 시선 비교를 물었다")

    return make


def basic_script(answer: Any = None) -> list[Any]:
    return [
        F.plan(assessments=[change_draft()]),
        F.tools(COMPARE),
        F.done(),
        answer or compare_answer(),
    ]


def final_ok(data: dict[str, Any]) -> FinalData:
    return FinalData.model_validate(data)


# ── 처음부터 끝까지 ─────────────────────────────────────────────────────────


def test_done_path_streams_progress_then_one_final() -> None:
    req = F.with_loaded(F.request(user_message=MSG), "t-4")
    llm = F.FakeLlm(basic_script())
    status, lines, data = F.run_turn(req, llm)
    assert status == 200
    assert [ln["event"] for ln in lines] == ["plan", "tool_done", "final"]
    assert [ln["seq"] for ln in lines] == [1, 2, 3]
    assert all(ln["run_id"] == "run-81" for ln in lines)
    fd = final_ok(data)
    assert fd.status == "DONE"
    assert fd.message is not None
    assert fd.message.text == "\n".join(c.text for c in fd.message.claims)
    # 판단은 코드 판정(28% < 31% → 나빠짐)이고 첫 문장은 템플릿이다
    a = fd.assessments[0]
    assert (a.verdict, a.verdict_source) == ("CONTRADICTED", "CODE")
    first = fd.message.claims[0].text
    assert first.startswith("말씀하신 'Take 4보다 Take 5에서 3장 시선이 좋아졌어'는 기록과 달라요:")
    # 비교 결론은 코드 문장이고 결론 계산 근거를 가리킨다
    assert "3장 청중 시선: Take 4의 31% → Take 5의 28%, 나빠졌어요." in fd.message.text
    # 답이 가리킨 근거와 그 입력이 evidence 에 함께 실린다
    ids = {e.evidence_id for e in fd.evidence}
    for c in fd.message.claims:
        assert set(c.evidence_ids) <= ids
    assert fd.meta.model == "fake-model"
    assert fd.usage.input_tokens == 400 and fd.usage.measured is True
    assert fd.budget_used.model_calls == 4 and fd.budget_used.tool_calls == 1
    assert fd.diagnostics is not None and fd.diagnostics.end.reason == "ANSWERED"


def test_progress_lines_hold_no_user_text_or_keys() -> None:
    req = F.with_loaded(F.request(user_message=MSG), "t-4")
    _, lines, _ = F.run_turn(req, F.FakeLlm(basic_script()))
    for ln in lines[:-1]:
        blob = json.dumps(ln, ensure_ascii=False)
        assert MSG not in blob
        assert "signature" not in blob


# ── 스트림 전 검사(모델 · 도구 0회) ─────────────────────────────────────────


@pytest.mark.parametrize(
    ("body", "status", "code"),
    [
        (b"{" * 10, 422, "VALIDATION_ERROR"),
        ("﻿{}".encode("utf-16"), 422, "VALIDATION_ERROR"),
        (b'{"a": NaN}', 422, "VALIDATION_ERROR"),
        (("[" * 40 + "1" + "]" * 40).encode(), 413, "AGENT_CONTEXT_TOO_LARGE"),
        (b"{}", 422, "VALIDATION_ERROR"),
    ],
)
def test_pre_stream_rejects_bad_body(body: bytes, status: int, code: str) -> None:
    llm = F.FakeLlm([])
    resp = handle_turn(body, F.deps(llm), secret=F.SECRET)
    assert resp.error is not None and (resp.error.status, resp.error.code) == (status, code)
    assert llm.calls == []


def test_pre_stream_request_too_large_413() -> None:
    llm = F.FakeLlm([])
    resp = handle_turn(b" " * (2 * 1024 * 1024 + 1), F.deps(llm), secret=F.SECRET)
    assert resp.error is not None and resp.error.status == 413


@pytest.mark.parametrize("secret", [None, b"short"])
def test_pre_stream_missing_or_short_secret_disables(secret: bytes | None) -> None:
    status, lines, body = F.run_turn(F.request(user_message=MSG), F.FakeLlm([]), secret=secret)
    assert (status, body["error"]["code"]) == (503, "AGENT_DISABLED")


def test_pre_stream_llm_unavailable_503() -> None:
    status, _, body = F.run_turn(F.request(user_message=MSG), None)
    assert (status, body["error"]["code"]) == (503, "LLM_UNAVAILABLE")


def test_pre_stream_context_too_large_413() -> None:
    req = F.request(user_message=MSG)
    req["context"]["pitch"]["missions"] = [{"text": "가" * 1000} for _ in range(200)]
    llm = F.FakeLlm([])
    status, _, body = F.run_turn(req, llm)
    assert (status, body["error"]["code"]) == (413, "AGENT_CONTEXT_TOO_LARGE")
    assert llm.calls == []


# ── 계획 단계 ──────────────────────────────────────────────────────────────


def test_out_of_scope_is_refused_after_plan() -> None:
    llm = F.FakeLlm([F.plan(route="OUT_OF_SCOPE", steps=[])])
    _, lines, data = F.run_turn(F.request(user_message="오늘 날씨 어때?"), llm)
    fd = final_ok(data)
    assert fd.status == "REFUSED"
    assert fd.message is not None and fd.message.text == templates.SystemText.OUT_OF_SCOPE.value
    assert len(llm.calls) == 1


@pytest.mark.parametrize(
    ("reason", "text"),
    [
        ("CONTENT_COACHING", templates.SystemText.CONTENT_COACHING),
        ("QA_PRACTICE", templates.SystemText.QA_PRACTICE),
        ("PRACTICE_PLAN", templates.SystemText.PRACTICE_PLAN),
        ("OTHER", templates.SystemText.OUT_OF_SCOPE),
        (None, templates.SystemText.OUT_OF_SCOPE),
        ("NOT_A_REASON", templates.SystemText.OUT_OF_SCOPE),
    ],
)
def test_out_of_scope_plan_refuses_with_fixed_sentence_per_reason(
    reason: str | None, text: templates.SystemText
) -> None:
    llm = F.FakeLlm([F.plan(route="OUT_OF_SCOPE", steps=[], scope_reason=reason)])
    _, _, data = F.run_turn(F.request(user_message="3장 대본 고쳐 줘"), llm)
    fd = final_ok(data)
    assert fd.status == "REFUSED"
    assert fd.message is not None and fd.message.text == text.value
    assert [c.kind for c in fd.message.claims] == ["SYSTEM"]
    assert len(llm.calls) == 1


@pytest.mark.parametrize(
    ("reason", "text"),
    [
        ("QA_PRACTICE", templates.SystemText.QA_PRACTICE),
        ("PRACTICE_PLAN", templates.SystemText.PRACTICE_PLAN),
        (None, templates.SystemText.OUT_OF_SCOPE),
    ],
)
def test_out_of_scope_answer_route_refuses_with_fixed_sentence_per_reason(
    reason: str | None, text: templates.SystemText
) -> None:
    llm = F.FakeLlm(
        [F.plan(route="ANSWER_DIRECT", steps=[])],
        answers=[F.answer([], route="OUT_OF_SCOPE", scope_reason=reason)],
    )
    _, _, data = F.run_turn(F.request(user_message="예상 질문 만들어 줘"), llm)
    fd = final_ok(data)
    assert fd.status == "REFUSED"
    assert fd.message is not None and fd.message.text == text.value


def test_mixed_request_appends_fixed_scope_sentences_and_stays_done() -> None:
    """섞인 요청: 범위 안 답 뒤에 이유별 고정 문장(중복 · 모르는 값 · 4개째는 버림)을 덧붙이고 DONE 이다."""
    req = F.with_loaded(F.request(user_message=MSG), "t-4")
    notes = [
        "CONTENT_COACHING",
        "CONTENT_COACHING",
        "NOPE",
        "QA_PRACTICE",
        "PRACTICE_PLAN",
        "OTHER",
    ]
    base = compare_answer()

    def with_notes(r: Any) -> Any:
        res = base(r)
        res.parsed["scope_notes"] = notes  # type: ignore[index]
        return res

    fd = final_ok(F.run_turn(req, F.FakeLlm(basic_script(with_notes)))[2])
    assert fd.status == "DONE"
    texts = [c.text for c in fd.message.claims]  # type: ignore[union-attr]
    st = templates.SystemText
    assert texts[-3:] == [st.CONTENT_COACHING.value, st.QA_PRACTICE.value, st.PRACTICE_PLAN.value]
    assert "3장 청중 시선: Take 4의 31% → Take 5의 28%, 나빠졌어요." in fd.message.text  # type: ignore[union-attr]
    assert [c.kind for c in fd.message.claims][-3:] == ["SYSTEM"] * 3  # type: ignore[union-attr]


def test_scope_rule_drops_model_sentence_without_fix_and_logs_check() -> None:
    """범위 밖 낱말이 든 모델 문장은 고치기에 보내지 않고 버린다. 고치기 호출이 없고 진단 기록에 SCOPE_RULE 이 남는다."""
    req = F.with_loaded(F.request(user_message=MSG), "t-4")
    bad = F.claim(
        "RECORD", "Take 5의 3장 청중 시선 비율은 28%예요. 이렇게 고쳐 쓰세요.", evidence_ids=[]
    )

    def make(r: Any) -> Any:
        ro = roles(r)
        bad["evidence_ids"] = [ro["current"]]
        return compare_answer(bad)(r)

    # 고치기 응답을 하나 준비해 두고, 쓰이지 않았는지(남은 응답 수)로 확인한다
    llm = F.FakeLlm(
        [*basic_script(make)], fixes=[F.fix({3: "Take 5의 3장 청중 시선 비율은 28%예요."})]
    )
    fd = final_ok(F.run_turn(req, llm)[2])
    assert fd.status == "DONE"
    assert all("이렇게 고쳐" not in c.text for c in fd.message.claims)  # type: ignore[union-attr]
    assert not [s for s in fd.diagnostics.steps if s.kind == "MODEL" and s.code == "FIX"]  # type: ignore[union-attr]
    steps = [s for s in fd.diagnostics.steps if s.kind == "CHECK" and s.code == "SCOPE_RULE"]  # type: ignore[union-attr]
    assert len(steps) == 1 and steps[0].detail.result == "DROPPED"


def test_check_diagnostics_use_original_sentence_numbers_after_fix_drops_earlier_sentence() -> None:
    """앞 문장이 고치기에서 버려져 번호가 밀려도, SCOPE_RULE 로 버린 문장 · 고친 뒤에도 실패한 문장의 CHECK 가 모두 남는다."""
    req = F.with_loaded(F.request(user_message=MSG), "t-4")
    s3 = F.claim("RECORD", "Take 5의 3장 청중 시선 비율은 77%예요.", evidence_ids=[])
    s4 = F.claim(
        "RECORD", "Take 5의 3장 청중 시선 비율은 28%예요. 이렇게 고쳐 쓰세요.", evidence_ids=[]
    )
    s5 = F.claim("RECORD", "Take 5의 3장 청중 시선 비율은 66%예요.", evidence_ids=[])

    def make(r: Any) -> Any:
        cur = roles(r)["current"]
        for c in (s3, s4, s5):
            c["evidence_ids"] = [cur]
        return compare_answer(s3, s4, s5)(r)

    llm = F.FakeLlm(
        [*basic_script(make)],
        fixes=[F.fix({3: None, 5: "Take 5의 3장 청중 시선 비율은 55%예요."})],
    )
    fd = final_ok(F.run_turn(req, llm)[2])
    steps = [s for s in fd.diagnostics.steps if s.kind == "CHECK" and s.code != "SUMMARY"]  # type: ignore[union-attr]
    dropped = sorted(
        s.detail.claim_index
        for s in steps
        if s.detail.result == "DROPPED" and s.detail.round in (0, 1)
    )
    assert 4 in dropped  # SCOPE_RULE 로 처음에 버린 문장
    assert [s.detail.claim_index for s in steps if s.detail.round == 1] == [
        5
    ]  # 고친 뒤에도 실패한 문장, 원래 번호


def test_plan_with_unbacked_number_falls_back_to_default_plan() -> None:
    bad_plan = F.plan(steps=["Take 5 시선 28% 확인", "답 쓰기"], assessments=[change_draft()])
    req = F.with_loaded(F.request(user_message=MSG), "t-4")
    _, lines, data = F.run_turn(
        req, F.FakeLlm([bad_plan, F.tools(COMPARE), F.done(), compare_answer()])
    )
    plan_line = lines[0]
    assert plan_line["event"] == "plan"
    assert all("28%" not in s["step"] for s in plan_line["data"]["plan"])


def test_uncovered_clause_gets_one_format_fix_then_template() -> None:
    """L6-16: 측정 절을 덮는 판단이 없으면 1회 고치게 하고, 그래도 없으면 '확인하지 못했어요' 템플릿."""
    req = F.with_loaded(F.request(user_message=MSG), "t-4")
    script = [F.plan(), F.plan(), F.tools(COMPARE), F.done()]
    llm = F.FakeLlm(
        script,
        answers=[F.answer([F.claim("RECORD", "기록을 확인했어요.")])],
        fixes=[F.fix({0: None})],
    )
    _, _, data = F.run_turn(req, llm)
    fd = final_ok(data)
    assert (
        llm.calls[1].purpose == "RESEARCH"
        and "<format_problems>" in llm.calls[1].messages[-1]["content"]
    )
    assert fd.message is not None
    assert (
        templates.UNCOVERED_CLAUSE.format(quote="Take 4보다 Take 5에서 3장 시선이 좋아졌어")
        in fd.message.text
    )


# ── 답 검사 · 한 번 고치기(6-9) ────────────────────────────────────────────


def test_wrong_number_is_fixed_once_then_passes() -> None:
    req = F.with_loaded(F.request(user_message=MSG), "t-4")
    llm = F.FakeLlm(
        [
            *basic_script(compare_answer(record_text="Take 5의 3장 청중 시선 비율은 30%예요.")),
            F.fix({2: "Take 5의 3장 청중 시선 비율은 28%예요."}),
        ]
    )
    _, _, data = F.run_turn(req, llm)
    fd = final_ok(data)
    assert llm.calls[-1].purpose == "FIX"
    assert "Take 5의 3장 청중 시선 비율은 28%예요." in fd.message.text  # type: ignore[union-attr]
    assert "30%" not in fd.message.text  # type: ignore[union-attr]
    checks = [s for s in fd.diagnostics.steps if s.kind == "CHECK"]  # type: ignore[union-attr]
    assert any(s.code == "NUMBER_MISMATCH" and s.detail.result == "FIX_REQUESTED" for s in checks)


def test_wrong_number_dropped_when_fix_fails() -> None:
    req = F.with_loaded(F.request(user_message=MSG), "t-4")
    llm = F.FakeLlm(
        [
            *basic_script(compare_answer(record_text="Take 5의 3장 청중 시선 비율은 30%예요.")),
            LlmTimeout(),
        ]
    )
    _, _, data = F.run_turn(req, llm)
    fd = final_ok(data)
    assert fd.status == "DONE"
    assert "30%" not in fd.message.text  # type: ignore[union-attr]


def test_model_conclusion_word_is_rejected_and_code_sentence_kept() -> None:
    """L4-25: 모델이 쓴 결론은 걸리고, 결론은 코드 템플릿만 남는다."""
    bad = F.claim("RECORD", "Take 5에서 3장 시선이 좋아졌어요.", evidence_ids=[])
    req = F.with_loaded(F.request(user_message=MSG), "t-4")
    llm = F.FakeLlm([*basic_script(compare_answer(bad)), F.fix({3: None})])
    _, _, data = F.run_turn(req, llm)
    fd = final_ok(data)
    assert "좋아졌어요" not in fd.message.text  # type: ignore[union-attr]


def test_model_text_copying_code_sentence_is_dropped() -> None:
    def make(r: Any) -> Any:
        ro = roles(r)
        code = "3장 청중 시선: Take 4의 31% → Take 5의 28%, 나빠졌어요."
        return F.answer(
            [
                F.claim("ASSESSMENT", "", assessment_index=0),
                F.claim("CONCLUSION", "", comparison_evidence_id=ro["outcome"]),
                F.claim("RECORD", code, evidence_ids=[ro["current"]]),
            ]
        )

    req = F.with_loaded(F.request(user_message=MSG), "t-4")
    _, _, data = F.run_turn(req, F.FakeLlm(basic_script(make)))
    fd = final_ok(data)
    texts = [c.text for c in fd.message.claims]  # type: ignore[union-attr]
    assert texts.count("3장 청중 시선: Take 4의 31% → Take 5의 28%, 나빠졌어요.") == 1


def test_all_claims_dropped_gives_verified_template_partial() -> None:
    def make(r: Any) -> Any:
        ro = roles(r)
        return F.answer(
            [F.claim("RECORD", "Take 5의 3장 시선은 99%예요.", evidence_ids=[ro["current"]])]
        )

    req = F.with_loaded(F.request(user_message="Take 5 3장 기록 알려 줘"), "t-4")
    llm = F.FakeLlm([F.plan(), F.tools(COMPARE), F.done()], answers=[make], fixes=[LlmTimeout()])
    _, _, data = F.run_turn(req, llm)
    fd = final_ok(data)
    assert fd.status in ("PARTIAL", "DONE")
    assert "99%" not in fd.message.text  # type: ignore[union-attr]


# ── 행동 멈춤 · 재개(5-6 · 5-8) ──────────────────────────────────────────────


def fetch_take_script() -> list[Any]:
    return [
        F.plan(assessments=[change_draft()]),
        F.tools(COMPARE),  # Take 4가 아직 없어 NOT_LOADED
        F.tools(("fetch_take", {"take_id": "t-4", "reason": "비교할 Take 4 기록"}), prefix="act"),
    ]


def resume_request(
    first_req: dict[str, Any], final: dict[str, Any], results: list[dict[str, Any]]
) -> dict[str, Any]:
    r = copy.deepcopy(first_req)
    r["run"]["trigger"] = "ACTION_RESULT"
    r["user_message"] = None
    r["choice"] = None
    r["resume"] = {"state": final["state"], "action_results": results}
    r["budget_used"] = final["budget_used"]
    return r


def fetched_take(take_id: str) -> dict[str, Any]:
    return take_snapshot(sample_pitch().take(take_id))  # type: ignore[arg-type]


def test_action_stops_with_signed_state_then_resume_finishes() -> None:
    req = F.request(user_message=MSG)
    llm = F.FakeLlm(fetch_take_script())
    _, lines, data = F.run_turn(req, llm)
    fd = final_ok(data)
    assert fd.status == "NEEDS_ACTION"
    action = fd.actions[0]
    assert action.type == "FETCH_TAKE" and action.args["take_id"] == "t-4"
    assert fd.state is not None and set(fd.state) == {"v", "kid", "payload", "signature"}
    assert fd.message is None  # 다시 평가가 아니면 판단을 먼저 내보내지 않는다
    # 재개: Take 4 결과를 받고 비교해 답한다
    res = [
        {
            "action_id": action.action_id,
            "type": "FETCH_TAKE",
            "status": "SUCCEEDED",
            "data": {"take": fetched_take("t-4")},
            "error": None,
        }
    ]
    req2 = resume_request(req, data, res)
    llm2 = F.FakeLlm([F.tools(COMPARE, prefix="c2"), F.done(), compare_answer()])
    _, lines2, data2 = F.run_turn(req2, llm2)
    fd2 = final_ok(data2)
    assert fd2.status == "DONE"
    assert "Take 4 기록을 가져왔어요." in fd2.message.text  # type: ignore[union-attr]
    assert any(
        c.kind == "ACTION_RESULT" and c.action_id == action.action_id for c in fd2.message.claims
    )  # type: ignore[union-attr]
    assert fd2.diagnostics.turn_call == 2  # type: ignore[union-attr]
    # 누적량은 이어진다(처음부터 다시 세지 않음)
    assert fd2.budget_used.model_calls == data["budget_used"]["model_calls"] + 3


@pytest.mark.parametrize("mutate", ["signature", "run_id", "user_id", "revision"])
def test_resume_with_tampered_state_or_ids_409(mutate: str) -> None:
    """L1-27: 서명 · 식별자 · 판 번호가 다르면 스트림 전 409, 모델 0회."""
    req = F.request(user_message=MSG)
    _, _, data = F.run_turn(req, F.FakeLlm(fetch_take_script()))
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
    req2 = resume_request(req, data, res)
    if mutate == "signature":
        sig = req2["resume"]["state"]["signature"]
        req2["resume"]["state"]["signature"] = ("A" if sig[0] != "A" else "B") + sig[1:]
    elif mutate == "run_id":
        req2["run"]["run_id"] = "run-other"
    elif mutate == "user_id":
        req2["run"]["user_id"] = "u-other"
    else:
        req2["run"]["state_revision"] = 1
    llm2 = F.FakeLlm([])
    status, _, body = F.run_turn(req2, llm2)
    assert (status, body["error"]["code"]) == (409, "AGENT_STATE_INVALID")
    assert llm2.calls == []


def test_resume_with_lower_budget_used_409() -> None:
    """L1-31: 누적 사용량을 줄여 보내 한도를 우회할 수 없다."""
    req = F.request(user_message=MSG)
    _, _, data = F.run_turn(req, F.FakeLlm(fetch_take_script()))
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
    req2 = resume_request(req, data, res)
    req2["budget_used"]["model_calls"] = 0
    status, _, body = F.run_turn(req2, F.FakeLlm([]))
    assert (status, body["error"]["code"]) == (409, "AGENT_STATE_INVALID")


@pytest.mark.parametrize("kind", ["other_id", "two_results", "other_type"])
def test_resume_with_unmatched_action_result_409(kind: str) -> None:
    """L1-30: 대기 행동과 결과가 정확히 1:1이 아니면 409."""
    req = F.request(user_message=MSG)
    _, _, data = F.run_turn(req, F.FakeLlm(fetch_take_script()))
    action = data["actions"][0]
    good = {
        "action_id": action["action_id"],
        "type": "FETCH_TAKE",
        "status": "SUCCEEDED",
        "data": {"take": fetched_take("t-4")},
        "error": None,
    }
    res = {
        "other_id": [{**good, "action_id": "run-81:a9"}],
        "two_results": [good, good],
        "other_type": [{**good, "type": "SAVE_GOAL"}],
    }[kind]
    status, _, body = F.run_turn(resume_request(req, data, res), F.FakeLlm([]))
    assert (status, body["error"]["code"]) == (409, "AGENT_STATE_INVALID")


def test_resume_with_wrong_take_in_result_stops_with_mismatch() -> None:
    req = F.request(user_message=MSG)
    _, _, data = F.run_turn(req, F.FakeLlm(fetch_take_script()))
    action = data["actions"][0]
    res = [
        {
            "action_id": action["action_id"],
            "type": "FETCH_TAKE",
            "status": "SUCCEEDED",
            "data": {"take": fetched_take("t-3")},
            "error": None,
        }
    ]
    llm2 = F.FakeLlm([])
    _, _, data2 = F.run_turn(resume_request(req, data, res), llm2)
    fd2 = final_ok(data2)
    assert fd2.status == "PARTIAL"
    assert fd2.message.text == templates.SystemText.ACTION_MISMATCH.value  # type: ignore[union-attr]
    assert llm2.calls == []


def test_resume_drops_removed_source_before_model() -> None:
    """L1-32: 이어 부를 때 지워진 원천에 기댄 결과는 모델 호출 전에 버린다."""
    req = F.request(user_message=MSG)
    _, _, data = F.run_turn(req, F.FakeLlm(fetch_take_script()))
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
    req2 = resume_request(req, data, res)
    req2["context"]["removed_sources"] = ["t-4"]
    _, _, data2 = F.run_turn(req2, F.FakeLlm([]))
    fd2 = final_ok(data2)
    assert fd2.status == "PARTIAL"
    assert fd2.message.text == templates.SystemText.SOURCE_DELETED_STOP.value  # type: ignore[union-attr]


# ── 한도(8-2 · 8-3) ────────────────────────────────────────────────────────


def test_seventh_turn_call_ends_without_model() -> None:
    """L1-31: 이어 부르기로 /agent/turn 6회를 넘기면 모델 없이 PARTIAL."""
    req = F.request(user_message=MSG)
    _, _, data = F.run_turn(req, F.FakeLlm(fetch_take_script()))
    from chat_agent import state as st

    payload = json.loads(st.base64.urlsafe_b64decode(data["state"]["payload"] + "=="))
    payload["turn_count"] = 6
    pl = st.StatePayload.model_validate(payload)
    signed = st.sign(pl, F.SECRET)
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
    llm2 = F.FakeLlm([])
    _, _, data2 = F.run_turn(req2, llm2)
    fd2 = final_ok(data2)
    assert fd2.status == "PARTIAL"
    assert fd2.message.text == templates.SystemText.TURN_LIMIT.value  # type: ignore[union-attr]
    assert llm2.calls == []


def test_research_stops_before_reserved_answer_calls() -> None:
    """L1-04 · 8-3: 호출 한도를 8로 줄여도 조사는 6번을 넘지 못하고, 답 쓰기 · 고치기 몫 2번은 남는다(기본 18은 test_limits.py)."""
    req = F.with_loaded(F.request(user_message=MSG), "t-4")
    reads = [
        ("get_take_report", {"take_id": t, "section": "slides", "slide_number": n})
        for t in ("t-5", "t-4")
        for n in (1, 2, 3, 4)
    ]
    loop_tools = [F.tools(r, prefix=f"l{i}") for i, r in enumerate(reads)]
    llm = F.FakeLlm(
        [F.plan(assessments=[change_draft()]), *loop_tools],
        answers=[F.answer([F.claim("ASSESSMENT", "", assessment_index=0)])],
    )
    _, _, data = F.run_turn(req, llm, cfg=load_config(max_model_calls=8))
    fd = final_ok(data)
    purposes = [c.purpose for c in llm.calls]
    assert purposes.count("RESEARCH") <= 6
    assert purposes[-1] == "ANSWER"
    assert fd.budget_used.model_calls <= 8


def test_repeated_same_tool_without_progress_stops_research() -> None:
    req = F.with_loaded(F.request(user_message=MSG), "t-4")
    llm = F.FakeLlm(
        [
            F.plan(assessments=[change_draft()]),
            F.tools(COMPARE, prefix="a"),
            F.tools(COMPARE, prefix="b"),
            compare_answer(),
        ]
    )
    _, _, data = F.run_turn(req, llm)
    fd = final_ok(data)
    # 같은 호출을 새 근거 없이 되풀이하면 조사를 멈추고 확인한 데까지만 말한다(L1-16)
    assert fd.status == "PARTIAL"
    assert fd.message.claims[0].text == templates.SystemText.NO_MORE_SOURCES.value  # type: ignore[union-attr]
    assert [c.purpose for c in llm.calls] == ["RESEARCH", "RESEARCH", "RESEARCH", "ANSWER"]


def test_answer_llm_failure_gives_template_partial_or_error() -> None:
    req = F.with_loaded(F.request(user_message=MSG), "t-4")
    llm = F.FakeLlm(
        [F.plan(assessments=[change_draft()]), F.tools(COMPARE), F.done(), LlmTimeout()]
    )
    _, lines, data = F.run_turn(req, llm)
    assert lines[-1]["event"] in ("final", "error")
    if lines[-1]["event"] == "final":
        fd = final_ok(data)
        assert fd.status == "PARTIAL"
        assert all(c.kind in ("SYSTEM", "RECORD") for c in fd.message.claims)  # type: ignore[union-attr]


def test_unknown_usage_is_null_and_not_measured() -> None:
    """L1-39: 공급자가 토큰 수를 주지 않으면 null · measured false 로 두고 0으로 확정하지 않는다."""
    req = F.with_loaded(F.request(user_message=MSG), "t-4")
    llm = F.FakeLlm(basic_script(), usage=Usage(None, None))
    _, _, data = F.run_turn(req, llm)
    fd = final_ok(data)
    assert fd.usage.input_tokens is None and fd.usage.measured is False
    assert fd.usage.missing_reason == "PROVIDER_USAGE_MISSING"


def test_output_too_large_ends_with_one_error_line() -> None:
    cfg = load_config(max_response_bytes=6_000, tail_reserve_bytes=1_024)
    req = F.with_loaded(F.request(user_message=MSG), "t-4")
    _, lines, data = F.run_turn(req, F.FakeLlm(basic_script()), cfg=cfg)
    assert lines[-1]["event"] == "error"
    assert data["error"]["code"] == "AGENT_OUTPUT_TOO_LARGE"
    assert sum(1 for ln in lines if ln["event"] in ("final", "error")) == 1


def test_client_disconnect_stops_new_calls() -> None:
    """L1-08: 반복자를 닫으면 새 모델 호출을 하지 않는다."""
    import threading

    gate = threading.Event()
    entered = threading.Event()
    finished = threading.Event()

    def slow_tools(_r: Any) -> Any:
        entered.set()  # 두 번째 모델 호출(조사)이 시작됐다
        gate.wait(2)
        return F.tools(COMPARE)

    req = F.with_loaded(F.request(user_message=MSG), "t-4")
    llm = F.FakeLlm([F.plan(assessments=[change_draft()]), slow_tools, F.done(), compare_answer()])
    log: list[dict[str, Any]] = []
    deps = F.deps(llm, log=log)
    resp = handle_turn(json.dumps(req, ensure_ascii=False).encode(), deps, secret=F.SECRET)
    assert resp.lines is not None
    first = next(resp.lines)
    assert json.loads(first)["event"] == "plan"
    assert entered.wait(2)  # 조사 호출 안에서 기다리는 중에 연결을 끊는다(타이밍에 기대지 않게)
    resp.lines.close()  # type: ignore[attr-defined]
    gate.set()
    # 실행이 끝까지 정리될 때까지(agent_end 로그) 기다린 뒤 호출 수를 본다
    for _ in range(100):
        if any(e.get("event") == "agent_end" for e in log):
            finished.set()
            break
        threading.Event().wait(0.02)
    assert finished.is_set()
    assert [c.purpose for c in llm.calls] == ["RESEARCH", "RESEARCH"]


# ── 자동 목표 확인(TAKE_REVIEWED) ────────────────────────────────────────────


def take_reviewed_request(goal: dict[str, Any] | None, take_id: str = "t-5") -> dict[str, Any]:
    req = json.loads((F.EXAMPLES / "request_take_reviewed.json").read_text(encoding="utf-8"))
    if goal is not None:
        req["context"]["conversation_goal"] = goal
    req["review_event"]["take_id"] = take_id
    return req


def test_take_reviewed_runs_without_model() -> None:
    req = take_reviewed_request(None)
    status, lines, data = F.run_turn(req, None)
    assert status == 200
    fd = final_ok(data)
    assert fd.status == "DONE"
    assert fd.goal_result is not None
    assert fd.meta.model is None
    assert fd.budget_used.model_calls == 0


def test_take_reviewed_unknown_when_goal_changed() -> None:
    """L1-39: 근거가 부족해도 goal_result 를 UNKNOWN · terminal false 로 명시한다."""
    req = take_reviewed_request(None)
    req["context"]["conversation_goal"] = None
    _, _, data = F.run_turn(req, None)
    fd = final_ok(data)
    assert fd.goal_result is not None
    assert (fd.goal_result.status, fd.goal_result.terminal) == ("UNKNOWN", False)


# ── 검증 반영 ──────────────────────────────────────────────────────────────


def test_fm_L1_39_partial_usage_keeps_unknown_side_null() -> None:
    req = F.with_loaded(F.request(user_message=MSG), "t-4")
    llm = F.FakeLlm(basic_script(), usage=Usage(100, None))
    fd = final_ok(F.run_turn(req, llm)[2])
    # 출력 사용량을 모르면 예약 전체를 쓴 것으로 두므로 출력 누적 8k 에 일찍 닿아 조사를 끝낸다(8-2 · 8-3)
    assert fd.usage.input_tokens == 300 and fd.usage.output_tokens is None
    assert fd.usage.measured is False
    assert fd.diagnostics is not None and fd.diagnostics.end.reason == "TOKENS"


def test_fm_L1_39_failed_attempt_usage_marks_unmeasured_and_logs_model_step() -> None:
    from chat_agent.llm_types import LlmResult

    req = F.with_loaded(F.request(user_message=MSG), "t-4")

    def retried(r: Any) -> Any:
        res = compare_answer()(r)
        return LlmResult(
            res.message,
            res.tool_calls,
            res.finish_reason,
            res.model,
            Usage(100, 20),
            2,
            res.parsed,
            [Usage(), Usage(100, 20)],
            ["LLM_UNAVAILABLE"],
        )

    llm = F.FakeLlm(basic_script(retried))
    fd = final_ok(F.run_turn(req, llm)[2])
    assert fd.usage.measured is False
    models = [s for s in fd.diagnostics.steps if s.kind == "MODEL" and s.code == "ANSWER"]  # type: ignore[union-attr]
    assert [m.detail.success for m in models] == [False, True]


def test_fm_L1_11_research_llm_failure_ends_partial() -> None:
    req = F.with_loaded(F.request(user_message=MSG), "t-4")
    llm = F.FakeLlm(
        [F.plan(assessments=[change_draft()]), LlmTimeout()],
        answers=[F.answer([F.claim("ASSESSMENT", "", assessment_index=0)])],
    )
    fd = final_ok(F.run_turn(req, llm)[2])
    assert fd.status == "PARTIAL"
    assert fd.message.claims[0].text == templates.SystemText.PARTIAL_CHECKED.value  # type: ignore[union-attr]


def test_fm_L1_38_failed_explanation_keeps_code_judgement_sentence() -> None:
    """모델 설명만 검사에 걸리고 고치기가 실패해도, 코드가 확정한 판단 첫 문장은 남긴다(6-3)."""

    def make(r: Any) -> Any:
        ro = roles(r)
        return F.answer(
            [
                F.claim("ASSESSMENT", "맞아요, 잘하셨어요.", assessment_index=0),
                F.claim("CONCLUSION", "", comparison_evidence_id=ro["outcome"]),
            ]
        )

    req = F.with_loaded(F.request(user_message=MSG), "t-4")
    llm = F.FakeLlm(
        [F.plan(assessments=[change_draft()]), F.tools(COMPARE), F.done()],
        answers=[make],
        fixes=[LlmTimeout()],
    )
    fd = final_ok(F.run_turn(req, llm)[2])
    a = [c for c in fd.message.claims if c.kind == "ASSESSMENT"]  # type: ignore[union-attr]
    assert a and a[0].text.startswith(
        "말씀하신 'Take 4보다 Take 5에서 3장 시선이 좋아졌어'는 기록과 달라요:"
    )
    assert "맞아요" not in fd.message.text  # type: ignore[union-attr]


def _two_fetches(req: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Take 4 가져오기 → 재개 → Take 3 가져오기에서 다시 멈춘 상태까지."""
    _, _, d1 = F.run_turn(req, F.FakeLlm(fetch_take_script()))
    a1 = d1["actions"][0]
    r1 = [
        {
            "action_id": a1["action_id"],
            "type": "FETCH_TAKE",
            "status": "SUCCEEDED",
            "data": {"take": fetched_take("t-4")},
            "error": None,
        }
    ]
    req2 = resume_request(req, d1, r1)
    req2["context"]["loaded_takes"] = [fetched_take("t-4")]
    _, _, d2 = F.run_turn(
        req2, F.FakeLlm([F.tools(("fetch_take", {"take_id": "t-3"}), prefix="act2")])
    )
    assert d2["status"] == "NEEDS_ACTION"
    return req2, d2


def test_fm_L1_32_removed_source_scrubs_previous_action_result_message() -> None:
    req = F.request(user_message=MSG)
    req2, d2 = _two_fetches(req)
    a2 = d2["actions"][0]
    r2 = [
        {
            "action_id": a2["action_id"],
            "type": "FETCH_TAKE",
            "status": "SUCCEEDED",
            "data": {"take": fetched_take("t-3")},
            "error": None,
        }
    ]
    req3 = resume_request(req2, d2, r2)
    req3["context"]["loaded_takes"] = []
    req3["context"]["removed_sources"] = ["t-4"]
    llm3 = F.FakeLlm([F.done()], answers=[F.answer([])])
    F.run_turn(req3, llm3)
    blob = "".join(str(m.get("content")) for m in llm3.calls[0].messages)
    assert "SOURCE_DELETED" in blob
    assert "Take 4 기록" not in blob and '"take_number": 4' not in blob


def test_fm_L1_32_changed_revision_between_calls_demotes_old_evidence() -> None:
    """재개 사이 Take 결과 판이 바뀌면 옛 근거는 새 문장의 근거가 아니고, 답 앞에 안내한다."""
    req = F.request(user_message=MSG)
    script = [
        F.plan(assessments=[change_draft()]),
        F.tools(("get_take_report", {"take_id": "t-5", "section": "slides", "slide_number": 3})),
        F.tools(("fetch_take", {"take_id": "t-4"}), prefix="act"),
    ]
    _, _, d1 = F.run_turn(req, F.FakeLlm(script))
    a1 = d1["actions"][0]
    r1 = [
        {
            "action_id": a1["action_id"],
            "type": "FETCH_TAKE",
            "status": "SUCCEEDED",
            "data": {"take": fetched_take("t-4")},
            "error": None,
        }
    ]
    req2 = resume_request(req, d1, r1)
    req2["context"]["pitch"]["focus_take"]["revisions"]["TAKE_RESULT"] += 1
    llm2 = F.FakeLlm([F.tools(COMPARE, prefix="c2"), F.done()], answers=[compare_answer()])
    fd = final_ok(F.run_turn(req2, llm2)[2])
    assert fd.message.claims[0].text == templates.SystemText.RECORD_CHANGED_DURING.value  # type: ignore[union-attr]
    assert fd.status in ("DONE", "PARTIAL")


def test_fm_L1_06_too_large_final_shrinks_diagnostics_before_dropping() -> None:
    from chat_agent.diagnostics import DiagRecorder
    from chat_agent.turn import _final_line, _Writer

    d = DiagRecorder(
        request_id="r",
        run_id="run-1",
        turn_call=1,
        versions={
            "feature_version": "f",
            "prompt_version": "p",
            "tool_schema": "t",
            "corpus_version": "c",
        },
        monotonic=lambda: 0.0,
    )
    for _ in range(300):
        d.step(
            "CHECK",
            "NUMBER_MISMATCH",
            {"claim_index": 0, "claim_kind": "RECORD", "round": 0, "result": "FIX_REQUESTED"},
        )
    diag = d.finish("DONE", "ANSWERED", max_bytes=10**9).model_dump(mode="json")
    full = len(json.dumps(diag, ensure_ascii=False))
    cfg = load_config(max_line_bytes=full // 2, max_response_bytes=full // 2 + 4096)
    out = json.loads(
        _final_line(_Writer("run-1", cfg), {"status": "DONE", "trace": [], "diagnostics": diag})
    )
    assert out["event"] == "final"
    assert (
        out["data"]["diagnostics"] is not None and out["data"]["diagnostics"]["truncated"] is True
    )


def _review_side(score: int, gaze: str) -> dict[str, Any]:
    from chat_agent_lab.fixtures import review

    r = review(take_id="t-5", dimensions={"audience_gaze": gaze}, score=score)
    r["criteria_results"] = []
    return r


def test_fm_L1_32_changed_revision_during_reassess_supersedes_exported_judgement() -> None:
    """다시 평가 전 내보낸 판단이 있고 대기 중 기록 판이 바뀌면, 새 자료로 다시 판단해 앞 판단을 대체로 잇는다."""
    req = F.with_loaded(F.request(user_message=MSG), "t-4")
    reassess = (
        "reassess",
        {"take_id": "t-5", "change": {"script_used": False}, "reason": "대본 조건"},
    )
    _, _, d1 = F.run_turn(
        req,
        F.FakeLlm(
            [
                F.plan(assessments=[change_draft()]),
                F.tools(COMPARE),
                F.tools(reassess, prefix="act"),
            ]
        ),
    )
    assert d1["status"] == "NEEDS_ACTION" and len(d1["assessments"]) == 1
    first_id = d1["assessments"][0]["assessment_id"]
    a1 = d1["actions"][0]
    data = {
        "before": _review_side(68, "PRIORITY"),
        "after": _review_side(70, "PRIORITY"),
        "official": {"score": 68, "dimensions": {"audience_gaze": "PRIORITY"}},
    }
    r1 = [
        {
            "action_id": a1["action_id"],
            "type": "REASSESS",
            "status": "SUCCEEDED",
            "data": data,
            "error": None,
        }
    ]
    req2 = resume_request(req, d1, r1)
    req2["context"]["pitch"]["focus_take"]["revisions"]["TAKE_RESULT"] += 1
    fd = final_ok(F.run_turn(req2, F.FakeLlm([F.done()], answers=[F.answer([])]))[2])
    ids = [a.assessment_id for a in fd.assessments]
    assert first_id in ids and len(ids) == 2
    new = next(a for a in fd.assessments if a.assessment_id != first_id)
    assert new.supersedes == [first_id]
    assert fd.message.claims[0].text == templates.SystemText.RECORD_CHANGED_DURING.value  # type: ignore[union-attr]


def test_fm_L1_12_research_refusal_ends_refused() -> None:
    from chat_agent.llm_types import LlmRefused

    req = F.with_loaded(F.request(user_message=MSG), "t-4")
    llm = F.FakeLlm([F.plan(assessments=[change_draft()]), LlmRefused()])
    fd = final_ok(F.run_turn(req, llm)[2])
    assert fd.status == "REFUSED"
    assert fd.message.text == templates.SystemText.CANNOT_ANSWER.value  # type: ignore[union-attr]
    assert [c.purpose for c in llm.calls] == ["RESEARCH", "RESEARCH"]


def test_failed_fetch_take_adds_fixed_notice() -> None:
    """가져오기 행동이 실패하면 7-10 고정 안내 "해당 연습 기록을 가져오지 못했어요."를 답에 붙인다."""
    req = F.request(user_message=MSG)
    _, _, d1 = F.run_turn(req, F.FakeLlm(fetch_take_script()))
    a1 = d1["actions"][0]
    r1 = [
        {
            "action_id": a1["action_id"],
            "type": "FETCH_TAKE",
            "status": "FAILED",
            "data": None,
            "error": {"code": "NOT_FOUND", "message": "없음"},
        }
    ]
    fd = final_ok(
        F.run_turn(resume_request(req, d1, r1), F.FakeLlm([F.done()], answers=[F.answer([])]))[2]
    )
    assert templates.SystemText.TAKE_FETCH_FAILED.value in fd.message.text  # type: ignore[union-attr]


def test_fm_L2_31_success_in_between_resets_repeated_failure() -> None:
    """같은 실패 행동은 '연속'일 때만 막는다: 실패 A → 다른 행동 성공 B → A 는 다시 실행할 수 있다(5-8)."""
    req = F.request(user_message=MSG)
    fetch3 = ("fetch_take", {"take_id": "t-3", "reason": "Take 3"})
    _, _, d1 = F.run_turn(
        req, F.FakeLlm([F.plan(assessments=[change_draft()]), F.tools(fetch3, prefix="a1")])
    )
    a1 = d1["actions"][0]
    r1 = [
        {
            "action_id": a1["action_id"],
            "type": "FETCH_TAKE",
            "status": "FAILED",
            "data": None,
            "error": {"code": "TIMEOUT", "message": "시간 초과"},
        }
    ]
    req2 = resume_request(req, d1, r1)
    _, _, d2 = F.run_turn(
        req2, F.FakeLlm([F.tools(("fetch_take", {"take_id": "t-4"}), prefix="a2")])
    )
    assert d2["status"] == "NEEDS_ACTION"
    a2 = d2["actions"][0]
    r2 = [
        {
            "action_id": a2["action_id"],
            "type": "FETCH_TAKE",
            "status": "SUCCEEDED",
            "data": {"take": fetched_take("t-4")},
            "error": None,
        }
    ]
    req3 = resume_request(req2, d2, r2)
    req3["context"]["loaded_takes"] = [fetched_take("t-4")]
    _, _, d3 = F.run_turn(
        req3,
        F.FakeLlm([F.tools(("fetch_take", {"take_id": "t-3", "reason": "다시"}), prefix="a3")]),
    )
    assert d3["status"] == "NEEDS_ACTION" and d3["actions"][0]["args"]["take_id"] == "t-3"


def test_value_question_is_not_judged_unverifiable() -> None:
    """값만 묻는 질문('3장 시선 몇 %였어?')은 주장이 아니다: '확인할 수 없어요' 판단 문장을 만들지 않는다."""
    msg = "Take 5 3장 시선 몇 %였어?"
    d = F.draft(
        "Take 5 3장 시선 몇 %였어",
        claim_op="VALUE",
        dimension="audience_gaze",
        metric="audience_ratio",
        slide_number=3,
        take_numbers=[5],
        polarity="NEUTRAL",
        verdict="UNVERIFIABLE",
    )
    report = ("get_take_report", {"take_id": "t-5", "section": "slides", "slide_number": 3})

    def ans(r: Any) -> Any:
        eid = F.find_evidence(r, lambda o: o.get("metric") == "audience_ratio")
        return F.answer(
            [F.claim("RECORD", "Take 5의 3장 청중 시선 비율은 28%예요.", evidence_ids=[eid])]
        )

    fd = final_ok(
        F.run_turn(
            F.request(user_message=msg),
            F.FakeLlm([F.plan(assessments=[d]), F.tools(report), F.done()], answers=[ans]),
        )[2]
    )
    assert "확인할 수 없어요" not in fd.message.text  # type: ignore[union-attr]
    assert "28%" in fd.message.text  # type: ignore[union-attr]
    assert all(a.type == "REQUEST" for a in fd.assessments)


def test_script_points_removed_notice_condition() -> None:
    """대본 허용으로 다시 평가해 점수가 오르면 7-10 "평가 항목이 빠져 오른 점수예요…"를 붙인다."""
    from chat_agent.loop import _script_points_removed

    before = {"score": 88, "dimensions": {"script_independence": "PRIORITY"}}
    after = {"score": 100, "dimensions": {"script_independence": "NOT_EVALUABLE"}}
    card = {
        "type": "REASSESS_RESULT",
        "change": {"script_used": True},
        "before": before,
        "after": after,
    }
    assert _script_points_removed(card) is True
    assert _script_points_removed({**card, "after": {**after, "score": 88}}) is False
    assert _script_points_removed({**card, "change": {"targets": []}}) is False
    # 대본 차원이 빠지지 않았으면 원인을 단정하지 않는다
    unchanged = {"score": 100, "dimensions": {"script_independence": "IMPROVED"}}
    assert _script_points_removed({**card, "after": unchanged}) is False


@pytest.mark.parametrize(
    ("quote", "message", "claim_op", "polarity", "kept"),
    [
        (
            "군더더기가 몇 번이나 나왔는데 문제없었어",
            "군더더기가 몇 번이나 나왔는데 문제없었어",
            "VALUE",
            "NEUTRAL",
            True,
        ),
        ("3장 시선 몇 %였어", "3장 시선 몇 %였어?", "VALUE", "NEUTRAL", False),
        ("3장 시선이 몇 %였는지 알아", "3장 시선이 몇 %였는지 알아.", "VALUE", "NEUTRAL", True),
        # 정보만 묻는 질문(값 · 방향 없음)은 요청이다
        ("졸업 발표 3장 어땠어?", "졸업 발표 3장 어땠어?", "OVERALL", "NEUTRAL", False),
        ("3장에서 뭐가 문제였어", "3장에서 뭐가 문제였어?", "ISSUE", "NEUTRAL", False),
        # 평가를 내세운 질문은 주장으로 판정한다
        ("Take 5 시선 어때, 잘 봤지?", "Take 5 시선 어때, 잘 봤지?", "OVERALL", "POSITIVE", True),
        ("3장 시선 별로였지? 왜", "3장 시선 별로였지? 왜", "OVERALL", "NEGATIVE", True),
    ],
)
def test_question_conversion_only_for_plain_questions(
    quote: str, message: str, claim_op: str, polarity: str, kept: bool
) -> None:
    """묻는 말이 있어도 값 · 방향 없이 물음표로 끝나는 단순 질문일 때만 요청으로 바꾼다."""
    from chat_agent import claims as clm
    from chat_agent.turn import _question_as_request

    d = clm.AssessmentDraft.model_validate(
        F.draft(
            quote,
            claim_op=claim_op,
            dimension="audience_gaze",
            metric=None,
            polarity=polarity,
        )
    )
    out = _question_as_request(d, message)
    assert (out.type == "MEASURED") is kept


def test_practice_move_only_answer_is_done_with_the_action() -> None:
    """'바로 발표할래'처럼 이동만 요청한 답은 문장이 없어도 DONE 이고 START_PRACTICE 행동 · 카드를 싣는다."""
    msg = "바로 발표할래"
    req = F.request(user_message=msg)
    req_draft = F.draft(msg, type_="REQUEST", claim_op=None, polarity="NEUTRAL")
    llm = F.FakeLlm(
        [
            F.plan(route="ANSWER_DIRECT", steps=[], assessments=[req_draft]),
            F.answer([], assessments=[req_draft], start_practice={"authorized_by": msg}),
        ]
    )
    status, lines, data = F.run_turn(req, llm)
    fd = final_ok(data)
    assert fd.status == "DONE"
    assert [a.type for a in fd.actions] == ["START_PRACTICE"]
    assert fd.message is not None
    assert fd.message.text == templates.SystemText.PRACTICE_BUTTON.value
    assert [c.type for c in fd.message.cards] == ["START_PRACTICE"]


def test_assessment_head_does_not_render_supported_template_for_request() -> None:
    """요청 · 정보 질문은 근거가 있어도 '기록과 맞아요'를 그리지 않는다(측정 주장은 그린다)."""
    from types import SimpleNamespace

    from chat_agent.turn import _assessment_head

    def outcome(type_: str) -> Any:
        a = SimpleNamespace(verdict="SUPPORTED", type=type_, quote="3장 시선 몇 %였어?")
        return SimpleNamespace(
            sentence="",
            extra_sentences=[],
            assessment=a,
            evidence_phrase="Take 5 3장 청중 시선 28%",
        )

    assert _assessment_head(outcome("REQUEST")) == ""
    assert _assessment_head(outcome("MEASURED")).startswith(
        "말씀하신 '3장 시선 몇 %였어?'는 기록과 맞아요"
    )
