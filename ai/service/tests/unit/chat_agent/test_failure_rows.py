"""실패 행 시험 보충 — 대응표(README '실패 행 대응표')에서 비어 있던 AI 몫 행의 단위 · 루프 · 계약 시험 (기능 문서 7 · 10-3).

가짜 모델(tests/unit/fakes.py)과 예시 요청으로 돌린다. 실제 모델 · 네트워크는 쓰지 않는다. 이름의 L 번호는 기능 문서 7절 실패 행이다.
"""

from __future__ import annotations

import copy
import json
from typing import Any

import pytest

from pitch_coach_ai.features.chat_agent.config import DEFAULT_CONFIG as CFG
from pitch_coach_ai.features.chat_agent.evidence import EvidenceRegistry
from pitch_coach_ai.features.chat_agent.llm_types import (
    LlmInvalidOutput,
    LlmRefused,
    LlmResult,
    ToolCall,
)
from pitch_coach_ai.features.chat_agent.runctx import RunCtx
from pitch_coach_ai.features.chat_agent.schemas import FinalData, TurnRequest
from pitch_coach_ai.features.chat_agent.tools import run_tool, validate_args
from pitch_coach_ai.features.chat_agent.turn import handle_turn

from . import fakes as F
from . import fixtures as fx

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


def pre_stream(body: bytes) -> tuple[int, str] | None:
    """스트림 전 결과: 오류면 (상태, 코드), 통과면 None. 줄은 읽지 않는다(모델 호출 0회)."""
    resp = handle_turn(body, F.deps(F.FakeLlm([])), secret=F.SECRET)
    return None if resp.error is None else (resp.error.status, resp.error.code)


def encode(req: dict[str, Any]) -> bytes:
    return json.dumps(req, ensure_ascii=False).encode("utf-8")


def padded(req: dict[str, Any], total_bytes: int) -> bytes:
    """닫는 중괄호 앞에 공백을 넣어 본문을 정확히 total_bytes 바이트로 만든다(JSON 으로는 같은 요청)."""
    body = encode(req)
    assert body.endswith(b"}") and len(body) <= total_bytes
    return body[:-1] + b" " * (total_bytes - len(body)) + b"}"


def first_final(req: dict[str, Any], llm: F.FakeLlm) -> dict[str, Any]:
    status, _lines, data = F.run_turn(req, llm)
    assert status == 200 and data is not None
    return data


def resume_with(
    first_req: dict[str, Any], final: dict[str, Any], result: dict[str, Any]
) -> dict[str, Any]:
    """BE 이어 부르기 흉내(가짜 BE 없이): 같은 요청에 서명 상태 · 결과 1개 · 직전 누적량."""
    r = copy.deepcopy(first_req)
    r["run"]["trigger"] = "ACTION_RESULT"
    r["user_message"] = None
    r["choice"] = None
    r["resume"] = {"state": final["state"], "action_results": [result]}
    r["budget_used"] = final["budget_used"]
    return r


def fetched(action: dict[str, Any]) -> dict[str, Any]:
    t = fx.sample_pitch().take(action["args"]["take_id"])
    assert t is not None
    return {
        "action_id": action["action_id"],
        "type": "FETCH_TAKE",
        "status": "SUCCEEDED",
        "data": {"take": fx.take_snapshot(t)},
        "error": None,
    }


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


# ── L1-02 요청 크기 · 깊이 · 기록 길이 경계 ──────────────────────────────────


def test_fm_L1_02_request_body_limit_is_utf8_bytes_with_boundary() -> None:
    """2 MiB 는 JSON 파싱 전에 UTF-8 바이트로 잰다: 정확히 2 MiB 는 통과, 1바이트 넘으면 413. 한글은 글자가 아니라 바이트로 센다."""
    limit = CFG.max_request_bytes
    req = F.request(user_message="가가 " + MSG)
    assert pre_stream(padded(req, limit)) is None
    assert pre_stream(padded(req, limit + 1)) == (413, "AGENT_CONTEXT_TOO_LARGE")
    over = padded(req, limit + 1)
    assert len(over.decode("utf-8")) < limit  # 글자 수로는 한도 안이지만 바이트로 넘는다


def test_fm_L1_02_request_json_depth_boundary() -> None:
    """요청 JSON 깊이 32 는 통과, 33 은 413(모르는 필드 안의 깊은 배열도 센다)."""
    req = F.request(user_message=MSG)

    def nest(k: int) -> Any:
        x: Any = 1
        for _ in range(k):
            x = [x]
        return x

    ok_req = {**req, "extra": nest(31)}  # 최상위 객체 1 + 배열 31 = 32
    deep_req = {**req, "extra": nest(32)}
    assert pre_stream(encode(ok_req)) is None
    assert pre_stream(encode(deep_req)) == (413, "AGENT_CONTEXT_TOO_LARGE")


def test_fm_L1_02_history_text_4000_boundary() -> None:
    """history 각 메시지 본문은 4,000자까지(코드 포인트). 4,001자는 자르지 않고 422."""

    def with_text(n: int) -> dict[str, Any]:
        req = F.request(user_message=MSG)
        req["history"]["messages"] = [
            {
                "role": "user",
                "text": "가" * n,
                "truncated": False,
                "basis": None,
                "stale_reasons": [],
            }
        ]
        return req

    assert pre_stream(encode(with_text(4000))) is None
    assert pre_stream(encode(with_text(4001))) == (422, "VALIDATION_ERROR")


def test_fm_L1_02_history_summary_items_boundary() -> None:
    """대화 요약은 항목 12개 · 각 120자까지(5-2 `SummaryItem`, 6-14). 넘으면 자르지 않고 422."""

    def with_summary(n: int, chars: int) -> dict[str, Any]:
        req = F.request(user_message=MSG)
        req["history"]["summary"] = [{"kind": "TOPIC", "text": "가" * chars}] * n
        return req

    assert pre_stream(encode(with_summary(12, 120))) is None
    assert pre_stream(encode(with_summary(13, 10))) == (422, "VALIDATION_ERROR")
    assert pre_stream(encode(with_summary(1, 121))) == (422, "VALIDATION_ERROR")


# ── L1-12 잘린 인자 · 거절 · 무효 출력 ───────────────────────────────────────


def test_fm_L1_12_unparseable_tool_args_are_not_executed() -> None:
    """잘린 인자(JSON 이 아님)의 도구 호출은 실행하지 않고 TOOL_INVALID 로 돌려준다(부분 인자 실행 0회)."""
    broken = LlmResult(
        message=None,
        tool_calls=[
            ToolCall(
                id="cut-0",
                name="compare_takes",
                args=None,
                raw_args='{"previous_take_id": "t-4", "current_take',
            )
        ],
        finish_reason="tool_calls",
        model="fake-model",
    )
    req = F.with_loaded(F.request(user_message=MSG), "t-4")
    llm = F.FakeLlm(
        [F.plan(assessments=[change_draft()]), broken, F.done()],
        answers=[F.answer([F.claim("ASSESSMENT", "", assessment_index=0)])],
        fixes=[F.fix({})],
    )
    data = first_final(req, llm)
    fd = FinalData.model_validate(data)
    assert all(t.outcome != "OK" for t in fd.trace)  # 비교 도구가 실행되지 않았다
    assert any("TOOL_INVALID" in json.dumps(m, ensure_ascii=False) for m in llm.calls[2].messages)


@pytest.mark.parametrize("err", [LlmRefused(), LlmInvalidOutput()])
def test_fm_L1_12_refused_or_invalid_research_output_runs_no_tool_and_is_not_resent(
    err: Exception,
) -> None:
    """조사 단계의 거절 · 무효 출력은 같은 호출을 다시 보내지 않고 도구 · 행동 없이 답 단계로 넘어간다."""
    req = F.with_loaded(F.request(user_message=MSG), "t-4")
    llm = F.FakeLlm(
        [F.plan(assessments=[change_draft()]), err],
        answers=[F.answer([F.claim("ASSESSMENT", "", assessment_index=0)])],
        fixes=[F.fix({})],
    )
    data = first_final(req, llm)
    fd = FinalData.model_validate(data)
    assert fd.status != "NEEDS_ACTION" and fd.actions == [] and fd.trace == []
    assert [c.purpose for c in llm.calls].count(
        "RESEARCH"
    ) == 2  # 계획 1 + 실패한 조사 1(다시 보내지 않음)


# ── L1-19 도구 구현의 예외 ───────────────────────────────────────────────────


def test_fm_L1_19_tool_exception_becomes_that_tools_error_and_the_run_goes_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """도구 구현이 예외로 끝나도 실행은 error 줄(INTERNAL_ERROR)로 끝나지 않는다: 그 호출이 오류 결과(`TOOL_FAILED`)가 되고, 단계에
    `확인 실패`가 붙고, 상황 신호 `TOOL_ERROR`를 본 모델이 다른 도구를 골라 확인하면 요청이 해소돼 DONE 이다. 예외 글은 줄 · 로그
    어디에도 없고, 서비스 로그 `agent_tool_crash`에 도구 이름 · 예외 종류 · 위치만 남는다."""
    from pitch_coach_ai.features.chat_agent.tools import TOOL_FAILED, impl

    from .test_plan_progress_turn import check_stream, compare_answer, compare_plan, decisions

    leak = "사용자 글 그대로 x7731"

    def boom(_ctx: Any, _a: Any) -> Any:
        raise ValueError(leak)

    monkeypatch.setitem(impl.RUNNERS, "get_take_report", boom)
    report = ("get_take_report", {"take_id": "t-5", "section": "slides", "slide_number": 3})
    llm = F.FakeLlm(
        [
            compare_plan(
                ("기록 보기", "get_take_report"),
                ("반복 확인", "compare_takes"),
                ("답 쓰기", "answer"),
            ),
            F.tools(report, prefix="a"),
            F.tools(COMPARE, prefix="b"),
            F.done(),
            compare_answer(),
        ]
    )
    log: list[dict[str, Any]] = []
    status, lines, data = F.run_turn(
        F.with_loaded(F.request(user_message=MSG), "t-4"), llm, log=log
    )
    assert status == 200 and lines[-1]["event"] == "final"
    fd = check_stream(lines, data)  # type: ignore[arg-type]
    assert fd.status == "DONE"
    # 도구 순서 · 결과: 실패한 기록 보기(ERROR) → 모델이 고른 비교(OK)
    assert [(t.tool, t.outcome) for t in fd.trace] == [
        ("get_take_report", "ERROR"),
        ("compare_takes", "OK"),
    ]
    live = [ln["data"]["trace"] for ln in lines if ln["event"] == "tool_done"]
    assert (live[0]["outcome"], live[0]["result_summary"]) == ("ERROR", "확인 실패")
    # 모델에게는 코드 오류만 돌려준다(다음 조사 호출의 도구 메시지)
    research = [c for c in llm.calls if c.purpose == "RESEARCH" and c.tools]
    told = [json.loads(m["content"]) for m in research[1].messages if m.get("role") == "tool"]
    assert told[-1]["ok"] is False and told[-1]["error"]["code"] == TOOL_FAILED
    # 단계: 실패한 단계는 확인 실패, 다른 도구로 확인한 뒤 끝나 실행하지 않은 단계는 없다
    assert data["plan"][0]["step"].endswith("확인 실패")  # type: ignore[index]
    assert decisions(log) == [
        ("TOOL_ERROR", "FACT_SHOWN", "CODE", 1),
        ("TOOL_ERROR", "ALT_TOOL", "LLM", 2),
    ]
    fact = next(d for d in log if d.get("event") == "agent_decision")
    assert fact["facts"] == [TOOL_FAILED]
    tool_steps = [s for s in fd.diagnostics.steps if s.kind == "TOOL"]  # type: ignore[union-attr]
    assert tool_steps[0].detail.error_code == TOOL_FAILED  # type: ignore[union-attr]
    (crash,) = [e for e in log if e.get("event") == "agent_tool_crash"]
    assert (crash["tool"], crash["seq"], crash["cause"]) == ("get_take_report", 1, "ValueError")
    assert not [e for e in log if e.get("event") == "agent_crash"]
    blob = json.dumps([lines, log], ensure_ascii=False)
    assert leak not in blob and "x7731" not in blob


# ── L1-25 한 응답에 행동이 여럿 ─────────────────────────────────────────────


def test_fm_L1_25_only_first_action_in_a_response_runs_and_skipped_calls_stay_skipped() -> None:
    """도구 → 행동 → 행동이 한 응답에 오면 앞 도구와 첫 행동까지만 처리하고, 남은 호출은 TOOL_SKIPPED 로 짝을 맞춘다."""
    req = F.request(user_message=MSG)
    one_response = F.tools(
        COMPARE,
        ("fetch_take", {"take_id": "t-4", "reason": "Take 4"}),
        ("fetch_take", {"take_id": "t-3", "reason": "Take 3"}),
        prefix="multi",
    )
    first = first_final(req, F.FakeLlm([F.plan(assessments=[change_draft()]), one_response]))
    fd = FinalData.model_validate(first)
    assert fd.status == "NEEDS_ACTION"
    assert [(a.type, a.args["take_id"]) for a in fd.actions] == [("FETCH_TAKE", "t-4")]
    assert [t.tool for t in fd.trace] == ["compare_takes"]  # 행동 앞의 도구는 실행됐다
    # 이어 부르면 건너뛴 호출(t-3)을 다시 실행하지 않고, 모델에 TOOL_SKIPPED 로 보인다
    llm2 = F.FakeLlm(
        [F.done()],
        answers=[F.answer([F.claim("ASSESSMENT", "", assessment_index=0)])],
        fixes=[F.fix({})],
    )
    second = first_final(resume_with(req, first, fetched(first["actions"][0])), llm2)
    assert FinalData.model_validate(second).status in ("DONE", "PARTIAL")
    seen = json.dumps(llm2.calls[0].messages, ensure_ascii=False)
    assert "TOOL_SKIPPED" in seen and "multi-2" in seen
    assert second["actions"] == []


def test_fm_L1_25_text_sent_with_an_action_is_not_shown() -> None:
    """답 글과 행동이 한 응답에 같이 오면 행동만 처리한다. 검사하지 않은 그 글은 final 에 나가지 않는다."""
    mixed = LlmResult(
        message="Take 4보다 확실히 좋아졌어요!",
        tool_calls=[
            ToolCall(
                id="mix-0",
                name="fetch_take",
                args={"take_id": "t-4", "reason": "Take 4"},
                raw_args=json.dumps({"take_id": "t-4", "reason": "Take 4"}),
            )
        ],
        finish_reason="tool_calls",
        model="fake-model",
    )
    data = first_final(
        F.request(user_message=MSG), F.FakeLlm([F.plan(assessments=[change_draft()]), mixed])
    )
    fd = FinalData.model_validate(data)
    assert fd.status == "NEEDS_ACTION" and len(fd.actions) == 1
    assert "확실히 좋아졌어요" not in json.dumps(data, ensure_ascii=False)


# ── L4-03 fallback 리뷰 ─────────────────────────────────────────────────────


def report_items(fallback: bool) -> tuple[list[dict[str, Any]], str | None]:
    req = F.request(user_message=MSG)
    review = req["context"]["pitch"]["focus_take"]["results"]["review"]
    review["fallback_reason"] = "LLM_UNAVAILABLE" if fallback else None
    tr = TurnRequest.model_validate(req)
    ctx = RunCtx.build(
        tr,
        cfg=CFG,
        registry=EvidenceRegistry(tr.run.run_id),
        scope_pitch_id="p-1",
        corpus=None,
        corpus_version="guides-1",
    )
    args = validate_args("get_take_report", {"take_id": "t-5", "section": "dimensions"})
    assert not isinstance(args, str)
    res, _ = run_tool(ctx, "get_take_report", args, seq=1, clock=lambda: 0.0)
    view = res.model_view()
    items = [{k: v for k, v in it.items() if k != "evidence_id"} for it in view["data"]["items"]]
    return items, view.get("note") or (view.get("data") or {}).get("note")


def test_fm_L4_03_fallback_review_still_gives_rule_score_and_dimensions() -> None:
    """리뷰 LLM 이 실패해 기본 문장이 나온 리뷰도 규칙 점수 · 차원은 그대로 쓰고, 기본 안내라고 표시한다."""
    normal, note_normal = report_items(False)
    fallback, note_fallback = report_items(True)
    assert fallback == normal and fallback
    assert note_normal is None
    assert note_fallback == "점수는 규칙으로 계산했고 설명은 기본 안내예요."


class _ReviewLlm:
    def __init__(self, out: Any) -> None:
        self.out = out

    def invoke(self, _messages: Any) -> Any:
        if isinstance(self.out, Exception):
            raise self.out
        return self.out


@pytest.mark.parametrize("llm", [None, _ReviewLlm(TimeoutError()), _ReviewLlm({"bad": 1})])
def test_fm_L4_03_review_rule_fields_do_not_depend_on_the_review_llm(llm: Any) -> None:
    """리뷰 LLM 이 없거나 시간 초과 · 잘못된 출력이어도 규칙 칸(점수 · 차원 · 사용자 기준 판정)과 판이 같다 — 다시 평가 비교의 전제(6-6)."""
    from pitch_coach_ai.features.review.core import review_take

    t5 = fx.sample_pitch().take("t-5")
    assert t5 is not None
    req = {
        "take_result": t5.take_result,
        "user_criteria": {
            "display_criteria": [],
            "targets": [
                {
                    "display": "청중 보기",
                    "metric": "audience_ratio",
                    "operator": "GTE",
                    "value": 0.3,
                }
            ],
            "script_used": None,
        },
    }
    base = review_take(copy.deepcopy(req), None).model_dump(mode="json")
    out = review_take(copy.deepcopy(req), llm).model_dump(mode="json")
    rule = ("score", "dimensions", "criteria_results")
    assert {k: out[k] for k in rule} == {k: base[k] for k in rule}
    assert out["meta"]["criteria_versions"] == base["meta"]["criteria_versions"]
