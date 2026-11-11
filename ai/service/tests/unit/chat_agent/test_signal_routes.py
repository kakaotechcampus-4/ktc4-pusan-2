"""상황 신호마다 이어지는 경로 · 실행 중 계획 고치기의 턴 시험 (기능 문서 6-1 7번 · 11-5 결정 로그, 목표 동작 1 · 4절).

가짜 모델(fakes.py)로 한 턴을 돌려, 신호(비교 불가 · 아직 불러오지 않음 · 리뷰 없음 · 기록 없음 · 도구 실패 · 시간 · 호출 부족 · 사용자
이의)마다 그 뒤의 도구 순서 · 할 일 목록 · 결정 로그 줄 · 끝 상태가 달라지는지 본다. 다른 파일에 이미 있는 경로는 다시 두지 않는다:
- 비교 불가 → 다른 대상(ALT_TOOL) · 계획 고치기(PLAN_EDIT) · 대응 없음(NONE), 아직 불러오지 않음 → 가져오기(ACTION), 시간 부족:
  test_plan_progress_turn.py
- 도구 실패 → 다른 도구로 확인(ALT_TOOL) → DONE: test_failure_rows.py `test_fm_L1_19_tool_exception_becomes_that_tools_error_and_the_run_goes_on`
기준표 대응 시험 목차는 연구 README '기준표 대응 시험 목차'.
"""

from __future__ import annotations

import json
from typing import Any

from pitch_coach_ai.features.chat_agent import progress
from pitch_coach_ai.features.chat_agent.config import load_config

from . import fakes as F
from .test_approval import REASSESS
from .test_none_evidence import no_take_request, none_ev
from .test_plan_progress_turn import (
    check_stream,
    compare_answer,
    compare_plan,
    decisions,
    plan_lines,
)
from .test_turn import COMPARE, MSG, final_ok

#: 리뷰 없음 · 기록 없음 신호를 모델에게 묻던 이전 동작(Issue #1194 4-G 의 코드 대응을 끈 설정)
LEGACY = load_config(code_signal_responses=False)


def research_blocks(llm: F.FakeLlm) -> list[dict[str, Any]]:
    """도구를 고르는 조사 호출마다 데이터 블록(`<data>…</data>`)."""
    out = []
    for c in llm.calls:
        if c.purpose == "RESEARCH" and c.tools:
            raw = c.messages[1]["content"]
            out.append(json.loads(raw[len("<data>\n") : -len("\n</data>")]))
    return out


def tools_of(data: dict[str, Any]) -> list[tuple[str, str]]:
    return [(t["tool"], t["outcome"]) for t in data["trace"]]


# ── 리뷰 없음(REVIEW_MISSING) → 코치 결과로 대신 읽기 ───────────────────────────


def test_review_missing_is_shown_then_the_model_reads_the_coach_result_instead() -> None:
    """리뷰가 없는 Take 의 리뷰 절을 읽으면 단계에 `리뷰 분석 전`이 보이고(FACT_SHOWN:CODE), 다음 조사 호출에 신호와 대응
    ('코치 결과로만 답하고 …')이 실린다. 모델이 같은 Take 의 문제 구간(코치 결과)을 읽으면 다른 대상(ALT_TOOL:LLM)이고, 그 실행이
    단계로 붙은 뒤 요청이 풀려 DONE 이다."""
    req = F.request(user_message="Take 5 리뷰에서 제일 큰 문제가 뭐였어?")
    req["context"]["pitch"]["focus_take"]["results"]["review"] = None
    review = ("get_take_report", {"take_id": "t-5", "section": "dimensions"})
    segments = ("get_take_report", {"take_id": "t-5", "section": "problem_segments"})
    llm = F.FakeLlm(
        [
            F.plan(steps=["리뷰 보기", "답 쓰기"], step_tools=["get_take_report", "answer"]),
            F.tools(review, prefix="a"),
            F.tools(segments, prefix="b"),
            F.done(),
        ],
        answers=[F.answer([F.claim("RECORD", "Take 5 점수는 70점이에요.")])],
        fixes=[F.fix({0: None})],
    )
    log: list[dict[str, Any]] = []
    # 모델이 신호에 대응하던 길(4-G 코드 대응을 끈 설정). 코드 대응 길은 test_signal_code.py
    _, lines, data = F.run_turn(req, llm, log=log, cfg=LEGACY)
    fd = check_stream(lines, data)
    assert tools_of(data) == [("get_take_report", "EMPTY"), ("get_take_report", "OK")]
    assert decisions(log) == [
        ("REVIEW_MISSING", "FACT_SHOWN", "CODE", 1),
        ("REVIEW_MISSING", "ALT_TOOL", "LLM", 2),
    ]
    blocks = research_blocks(llm)
    assert "signals" not in blocks[0]
    assert blocks[1]["signals"] == [
        {
            "signal": "REVIEW_MISSING",
            "facts": [],
            "options": list(progress.SIGNAL_OPTIONS["REVIEW_MISSING"]),
        }
    ]
    assert "signals" not in blocks[2]  # 대응한 뒤에는 신호가 닫혔다
    # 리뷰 단계는 사실을 보이고 끝났고, 문제 구간 읽기는 실행한 뒤에 단계로 붙었다(답 쓰기 앞)
    plans = plan_lines(lines)
    assert plans[1][0] == {
        "step": "리뷰 보기: Take 5 기록 → 리뷰 분석 전",
        "status": "DONE",
        "note": None,
    }
    assert len(plans[1]) == 2 and len(plans[-1]) == 3
    # 모델이 고른 대안도 까닭과 함께 보인다(Issue #1194 4-F)
    assert data["plan"][1]["step"].startswith("리뷰 없어 코치 기록: Take 5 기록 → ")
    assert fd.status == "DONE"
    assert [p["status"] for p in data["plan"]] == ["DONE", "DONE", "DONE"]


# ── 읽은 범위에 기록 없음(NO_RECORD) → '없음'을 근거로 답 ─────────────────────────


def test_no_record_offers_the_none_answer_and_the_model_answers_without_more_reads() -> None:
    """범위 피치의 Take 가 0개(`Take 없음`)면 다음 조사 호출에 '없음'을 근거로 답하는 대응이 실린다. 모델이 더 읽지 않고 답하면
    대응 없음(NONE:LLM)으로 닫히고, '없음' 근거 문장으로 요청이 풀려 DONE 이다(연습 준비 버튼, 부분 답 안내 없음)."""

    def answer(r: Any) -> Any:
        return F.answer(
            [
                F.claim(
                    "RECORD",
                    "'졸업 발표'에는 아직 연습한 Take가 없어요.",
                    evidence_ids=[none_ev(r)],
                )
            ]
        )

    llm = F.FakeLlm(
        [
            F.plan(steps=["최근 발표 찾기", "답 쓰기"], step_tools=["list_pitches", "answer"]),
            F.tools(("list_pitches", {})),
            F.done(),
        ],
        answers=[answer],
    )
    log: list[dict[str, Any]] = []
    _, lines, data = F.run_turn(no_take_request(), llm, log=log, cfg=LEGACY)
    fd = check_stream(lines, data)
    assert tools_of(data) == [("list_pitches", "OK")]
    assert decisions(log) == [
        ("NO_RECORD", "FACT_SHOWN", "CODE", 1),
        ("NO_RECORD", "NONE", "LLM", None),
    ]
    assert research_blocks(llm)[1]["signals"] == [
        {
            "signal": "NO_RECORD",
            "facts": ["TAKES"],
            "options": list(progress.SIGNAL_OPTIONS["NO_RECORD"]),
        }
    ]
    assert fd.status == "DONE"
    assert data["plan"][0]["step"] == "최근 발표 찾기: 피치 목록 → Take 없음"
    assert [(a.type, a.autonomy) for a in fd.actions] == [("START_PRACTICE", "BUTTON")]


# ── 도구 실패(TOOL_ERROR) → 복구하지 않으면 PARTIAL ──────────────────────────────


def test_tool_error_without_recovery_closes_with_none_and_the_answer_decides_the_end() -> None:
    """확인한 뒤 다음 도구가 실패하고(범위 밖 Take: NOT_FOUND) 모델이 다른 도구로 확인하지 않으면, 신호는 대응 없음(NONE:LLM)으로
    닫힌다. 끝 상태는 답이 요청을 풀었는지로 정한다: 모델 답이 비교 질문을 근거 문장으로 풀었으면 DONE 이고 실패 단계는 `확인 실패`
    글로 남되 DONE 이 아니다(끝 처리로 SKIPPED). 다른 경로:
    - 답 문장이 검사로 빠져 코드 문장으로 답하면 풀리지 않은 실패 단계 때문에 PARTIAL · TODO: test_none_evidence.py
      `test_recovered_tool_failure_is_done_but_unrecovered_is_partial`
    - 다른 도구로 확인(ALT_TOOL) → DONE: test_failure_rows.py 의 L1-19 시험"""
    bad = ("get_take_report", {"take_id": "t-9", "section": "problem_segments"})
    llm = F.FakeLlm(
        [
            compare_plan(
                ("반복 확인", "compare_takes"),
                ("문제 구간 보기", "get_take_report"),
                ("답 쓰기", "answer"),
            ),
            F.tools(COMPARE, prefix="a"),
            F.tools(bad, prefix="b"),
            F.done(),
            compare_answer(),
        ]
    )
    log: list[dict[str, Any]] = []
    _, lines, data = F.run_turn(F.with_loaded(F.request(user_message=MSG), "t-4"), llm, log=log)
    fd = check_stream(lines, data)
    assert tools_of(data) == [("compare_takes", "OK"), ("get_take_report", "ERROR")]
    assert decisions(log) == [
        ("TOOL_ERROR", "FACT_SHOWN", "CODE", 2),
        ("TOOL_ERROR", "NONE", "LLM", None),
    ]
    fact = next(d for d in log if d.get("event") == "agent_decision")
    assert fact["facts"] == ["NOT_FOUND"] and fact["step"] == 1
    assert research_blocks(llm)[2]["signals"][0]["options"] == list(
        progress.SIGNAL_OPTIONS["TOOL_ERROR"]
    )
    assert fd.status == "DONE"
    assert data["plan"][1]["step"].endswith("확인 실패")
    # 실행했지만 실패한 단계는 '의도적으로 건너뜀'이 아니다: 글에 실패를 보인 채 DONE
    assert [p["status"] for p in data["plan"]] == ["DONE", "DONE", "DONE"]


# ── 호출 부족(BUDGET_LOW) → 코드가 부분 결과로 끝냄 ──────────────────────────────


def test_tool_call_limit_stops_research_with_budget_low_and_time_short_step() -> None:
    """도구 호출 한도에 닿으면 남은 요청은 실행하지 않고(도구 기록에 없음) 조사를 끝낸다. 지금 하던 단계에 `시간 부족`이 붙어 남은
    일(TODO)이고, 결정 로그는 BUDGET_LOW · STOP_PARTIAL:CODE(까닭 TOOL_CALLS), 끝 상태는 PARTIAL 이다. 시간 한도 경로는
    test_plan_progress_turn.py `test_partial_on_research_time_leaves_todo_and_time_short`."""
    report = ("get_take_report", {"take_id": "t-5", "section": "problem_segments"})
    llm = F.FakeLlm(
        [
            compare_plan(
                ("반복 확인", "compare_takes"),
                ("문제 구간 보기", "get_take_report"),
                ("답 쓰기", "answer"),
            ),
            F.tools(COMPARE, prefix="a"),
            F.tools(report, prefix="b"),
        ],
        answers=[compare_answer()],
    )
    log: list[dict[str, Any]] = []
    _, lines, data = F.run_turn(
        F.with_loaded(F.request(user_message=MSG), "t-4"),
        llm,
        log=log,
        cfg=load_config(max_tool_calls=1),
    )
    fd = check_stream(lines, data)
    assert tools_of(data) == [("compare_takes", "OK")]  # 한도 뒤 요청은 실행하지 않았다
    assert fd.status == "PARTIAL" and fd.diagnostics.end.reason == "TOOL_CALLS"  # type: ignore[union-attr]
    assert decisions(log) == [("BUDGET_LOW", "STOP_PARTIAL", "CODE", None)]
    line = next(d for d in log if d.get("event") == "agent_decision")
    assert (line["facts"], line["step"]) == (["TOOL_CALLS"], 1)
    assert data["plan"][1] == {"step": "문제 구간 보기: 시간 부족", "status": "TODO", "note": None}
    assert [p["status"] for p in data["plan"]] == ["DONE", "TODO", "DONE"]


# ── 사용자 이의(USER_DISPUTE) → 다시 평가 행동 ───────────────────────────────────


def test_user_dispute_with_a_condition_becomes_a_reassess_action_logged_as_llm_action() -> None:
    """조건을 말한 이의("대본 보고 한 발표였어. 다시 평가해 줘")에 모델이 다시 평가를 고르면 허용 검사를 통과해 결정 로그
    USER_DISPUTE · ACTION:LLM 한 줄이 남고, 답 문장을 먼저 낸 뒤 확인 카드와 함께 NEEDS_ACTION(ASK)으로 멈춘다. 할 일 목록은
    멈춘 때 상태 그대로다(끝 처리하지 않음)."""
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
        [
            F.plan(
                steps=["이의 확인", "다시 평가 요청"],
                step_tools=[None, None],
                assessments=d,
            ),
            F.tools(REASSESS, prefix="ra"),
        ],
        answers=[F.answer([F.claim("ASSESSMENT", "", assessment_index=0)])],
        fixes=[F.fix({})],
    )
    log: list[dict[str, Any]] = []
    _, lines, data = F.run_turn(F.request(user_message=msg), llm, log=log)
    fd = check_stream(lines, data)
    assert fd.status == "NEEDS_ACTION"
    assert [(a.type, a.autonomy) for a in fd.actions] == [("REASSESS", "ASK")]
    assert [c.type for c in fd.message.cards] == ["ASK_CONFIRM"]  # type: ignore[union-attr]
    assert decisions(log) == [("USER_DISPUTE", "ACTION", "LLM", None)]
    assert data["trace"] == []  # 기록 도구 없이 행동으로 멈췄다
    assert [p["status"] for p in data["plan"]] == ["DOING", "TODO"]
    events = [ln["event"] for ln in lines]
    assert events.index("claim") < events.index("final")  # 답 문장을 먼저 낸 뒤 멈춤


def test_reassess_without_a_condition_is_not_a_dispute_action() -> None:
    """조건 없는 측정값 이의("시선 좋았는데 다시 평가해 줘")에 모델이 다시 평가를 골라도 허용 검사에 걸려 행동이 없고(TOOL_ERROR 로
    모델에 돌려줌), USER_DISPUTE 결정 로그도 남지 않는다(6-6)."""
    msg = "Take 5 시선 좋았는데 다시 평가해 줘"
    d = [
        F.draft(
            "Take 5 시선 좋았는데",
            claim_op="VALUE",
            dimension="audience_gaze",
            metric="audience_ratio",
            take_numbers=[5],
            polarity="POSITIVE",
        ),
        F.draft("다시 평가해 줘", type_="REQUEST", claim_op=None, polarity="NEUTRAL"),
    ]
    reassess = ("reassess", {"take_id": "t-5", "change": {}, "reason": "이의"})
    llm = F.FakeLlm(
        [F.plan(assessments=d), F.tools(reassess, prefix="ra"), F.done()],
        answers=[F.answer([F.claim("ASSESSMENT", "", assessment_index=0)])],
        fixes=[F.fix({})],
    )
    log: list[dict[str, Any]] = []
    _, lines, data = F.run_turn(F.request(user_message=msg), llm, log=log)
    fd = final_ok(data)  # type: ignore[arg-type]
    assert fd.status != "NEEDS_ACTION" and not [a for a in fd.actions if a.type == "REASSESS"]
    assert all(s != "USER_DISPUTE" for s, *_ in decisions(log))


# ── 실행 중 계획 고치기(update_plan): 둘째 plan 줄 · final.plan · 넷째 고치기 한도 ─────────────


def _edit(n: int, *names_tools: tuple[str, str | None]) -> Any:
    steps = [
        {"step": s, "status": "DOING" if i == 0 else "TODO", "note": None, "tool": t}
        for i, (s, t) in enumerate(names_tools)
    ]
    return F.tools(("update_plan", {"steps": steps}), prefix=f"u{n}")


def test_model_plan_edits_mid_run_show_at_once_and_the_fourth_is_a_limit_error() -> None:
    """모델이 실행 중에 계획을 고치면 그 자리에서 plan 줄이 나가고(도구 기록 줄보다 먼저), 이미 실행한 단계는 결과 글 그대로 앞에
    남는다. 고친 목록의 단계에 맞는 도구가 실행되면 그 단계가 채워진다. 넷째 고치기는 모델에 `LIMIT` 오류로 돌아가고 plan 줄이 없다.
    코드의 표시 갱신(도구마다)은 몇 번이어도 계획 수정 한도(3번)에 세지 않는다. 끝 줄 `final.plan`은 마지막 plan 줄과 같고,
    셋째 고치기의 실행하지 않은 단계만 SKIPPED 다."""
    req = F.with_loaded(F.request(user_message=MSG), "t-4")
    seen: dict[str, Any] = {}

    def after_fourth(r: Any) -> Any:
        seen["fourth"] = F.tool_results(r)[-1]
        return F.done()

    report = ("get_take_report", {"take_id": "t-5", "section": "problem_segments"})
    llm = F.FakeLlm(
        [
            compare_plan(("기록 확인", None), ("답 쓰기", "answer")),
            F.tools(COMPARE, prefix="c"),
            _edit(1, ("문제 구간 보기", "get_take_report"), ("답 정리", "answer")),
            F.tools(report, prefix="r"),
            _edit(2, ("자료 찾기", "search_guides"), ("답 정리", "answer")),
            _edit(3, ("목표 확인", "get_goals"), ("답 정리", "answer")),
            _edit(4, ("넷째 고치기", None), ("답 정리", "answer")),
            after_fourth,
            compare_answer(),
        ]
    )
    _, lines, data = F.run_turn(req, llm)
    fd = check_stream(lines, data)
    plans = plan_lines(lines)
    ran = "기록 확인: Take 4·5 비교 → 3장 청중 시선 나빠짐"
    # 처음 계획 → 비교 뒤 표시 갱신 → 첫 고치기(실행한 단계 앞에 남김 + 새 단계)
    assert [s["step"] for s in plans[0]] == ["기록 확인", "답 쓰기"]
    assert [(s["step"], s["status"]) for s in plans[1]] == [(ran, "DONE"), ("답 쓰기", "DOING")]
    assert [(s["step"], s["status"]) for s in plans[2]] == [
        (ran, "DONE"),
        ("문제 구간 보기", "DOING"),
        ("답 정리", "TODO"),
    ]
    events = [ln["event"] for ln in lines]
    first_edit_line = [i for i, e in enumerate(events) if e == "plan"][2]
    edit_traces = [
        i
        for i, ln in enumerate(lines)
        if ln["event"] == "tool_done" and ln["data"]["trace"]["tool"] == "update_plan"
    ]
    assert first_edit_line < edit_traces[0]  # 고친 목록이 그 도구 기록 줄보다 먼저 나간다
    # 고친 목록의 단계에 맞는 도구(문제 구간 읽기)가 그 단계를 채웠다
    assert plans[3][1]["step"].startswith("문제 구간 보기: Take 5 기록 → ")
    assert plans[3][1]["status"] == "DONE"
    # 넷째 고치기: 모델에는 한도 오류, 화면 목록에는 들어가지 않음
    assert seen["fourth"]["ok"] is False and seen["fourth"]["error"]["code"] == "LIMIT"
    assert not any(s["step"] == "넷째 고치기" for p in plans for s in p)
    # 도구 기록도 실제와 같다: 거절된 넷째 고치기는 OK 가 아니라 ERROR · '고치기 한도'
    last_edit = lines[edit_traces[-1]]["data"]["trace"]
    assert (last_edit["outcome"], last_edit["result_summary"]) == ("ERROR", "고치기 한도")
    limits = [s for s in fd.diagnostics.steps if s.kind == "LIMIT"]  # type: ignore[union-attr]
    assert [(s.code, s.detail.used) for s in limits] == [("PLAN_EDITS", 3)]  # type: ignore[union-attr]
    # 표시 갱신은 고치기보다 많이 나갔다(도구 두 번 + 끝 처리) — 한도와 상관없음
    model_edit_lines = 3
    assert len(plans) > 1 + model_edit_lines + 2
    # 끝: 실행한 두 단계 DONE, 셋째 고치기의 실행하지 않은 단계는 끝 목록에서 빠지고, 답 정리는 답이 있어 DONE
    assert fd.status == "DONE"
    assert [(s["step"].split(":")[0], s["status"]) for s in data["plan"]] == [
        ("기록 확인", "DONE"),
        ("문제 구간 보기", "DONE"),
        ("답 정리", "DONE"),
    ]
    assert data["plan"] == plans[-1]
