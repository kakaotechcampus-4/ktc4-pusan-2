"""할 일 단계 = 실제 실행 · 재계획 신호 · 결정 로그의 턴 시험 (progress.py, 기능 문서 5-4 · 5-5 · 6-1 7번 · 11-5).

가짜 모델(fakes.py)로 한 턴을 돌려 실시간 plan 줄 · 최종 `final.plan` · 결정 로그(`agent_decision`, 서비스 로그)를 본다.
"""

from __future__ import annotations

import copy
import json
import re
from typing import Any

import pytest

from pitch_coach_ai.features.chat_agent import progress, prompts
from pitch_coach_ai.features.chat_agent.config import load_config
from pitch_coach_ai.features.chat_agent.schemas import FinalData

from . import fakes as F
from .test_turn import COMPARE, MSG, change_draft, fetched_take, resume_request

STREAM_EVENTS = {"plan", "tool_done", "claim", "final", "error"}
ACTION_TYPES = {"FETCH_PITCH", "FETCH_TAKE", "REASSESS", "SAVE_GOAL", "START_PRACTICE"}
_ENUM = re.compile(r"^[A-Z_]+$")


def plan_lines(lines: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    return [ln["data"]["plan"] for ln in lines if ln["event"] == "plan"]


def decisions(log: list[dict[str, Any]]) -> list[tuple[str, str, str, int | None]]:
    return [
        (d["signal"], d["effect"], d["decider"], d["tool_seq"])
        for d in log
        if d.get("event") == "agent_decision"
    ]


def check_stream(
    lines: list[dict[str, Any]], data: dict[str, Any], *, live_is_final: bool = True
) -> FinalData:
    """새 줄 종류 · 행동 종류 없음, 최종 목록 = 마지막 plan 줄(실시간 화면 = 최종, `plan_progress`가 켜졌을 때)."""
    assert {ln["event"] for ln in lines} <= STREAM_EVENTS
    fd = FinalData.model_validate(data)
    assert {a.type for a in fd.actions} <= ACTION_TYPES
    if live_is_final:
        assert data["plan"] == plan_lines(lines)[-1]
    return fd


def compare_plan(*steps: tuple[str, str | None], **kw: Any) -> Any:
    return F.plan(
        steps=[s for s, _ in steps],
        step_tools=[t for _, t in steps],
        assessments=[change_draft()],
        **kw,
    )


def compare_answer(*, record: bool = True) -> Any:
    """비교 결론 · 값 문장 답(비교할 수 있었던 compare_takes 결과의 근거로). `record`면 Take 5 값 문장도 쓴다."""

    def make(r: Any) -> Any:
        ro = next(
            d["evidence_by_role"]
            for res in F.tool_results(r)
            if "outcome" in ((d := res.get("data") or {}).get("evidence_by_role") or {})
        )
        return F.answer(
            [
                F.claim(
                    "ASSESSMENT", "3장에서 청중을 보는 시간을 늘려 보세요.", assessment_index=0
                ),
                F.claim("CONCLUSION", "", comparison_evidence_id=ro["outcome"]),
                *(
                    [
                        F.claim(
                            "RECORD",
                            "Take 5의 3장 청중 시선 비율은 28%예요.",
                            evidence_ids=[ro["current"]],
                        )
                    ]
                    if record
                    else []
                ),
            ]
        )

    return make


def other_script_take3(req: dict[str, Any]) -> dict[str, Any]:
    """Take 3 의 대본 버전을 다르게 한다(Take 3 · 5 는 비교할 수 없음)."""
    req = copy.deepcopy(req)
    for t in req["context"]["pitch"]["takes"]:
        if t["take_id"] == "t-3":
            t["script_version_id"] = "sv-0"
    for snap in req["context"]["loaded_takes"]:
        if snap["summary"]["take_id"] == "t-3":
            snap["summary"]["script_version_id"] = "sv-0"
    return req


# ── 1. 단계 = 실행 ──────────────────────────────────────────────────────────


def test_steps_follow_actual_runs_and_unrun_steps_are_skipped() -> None:
    req = F.with_loaded(F.request(user_message=MSG), "t-4")
    llm = F.FakeLlm(
        [
            compare_plan(
                ("반복 확인", "compare_takes"),
                ("발표법 자료 찾기", "search_guides"),
                ("답 쓰기", "answer"),
            ),
            F.tools(COMPARE),
            F.done(),
            compare_answer(),
        ]
    )
    log: list[dict[str, Any]] = []
    _, lines, data = F.run_turn(req, llm, log=log)
    fd = check_stream(lines, data)
    assert fd.status == "DONE"
    plans = plan_lines(lines)
    assert [p["status"] for p in plans[0]] == ["DOING", "TODO", "TODO"]
    # 도구가 끝나면 그 단계가 DONE + `목적: 실행 → 결과`, 다음 단계가 DOING
    assert plans[1][0] == {
        "step": "반복 확인: Take 4·5 비교 → 3장 청중 시선 나빠짐",
        "status": "DONE",
        "note": None,
    }
    assert [p["status"] for p in plans[1]] == ["DONE", "DOING", "TODO"]
    # 끝(DONE): 실행하지 않은 단계는 SKIPPED, 답 쓰기 단계는 답 문장이 있어 DONE
    assert [p["status"] for p in data["plan"]] == ["DONE", "SKIPPED", "DONE"]
    assert data["plan"][1]["step"] == "발표법 자료 찾기"
    events = [ln["event"] for ln in lines]
    assert events.index("tool_done") < 2 < events.index("final")  # 표시 갱신은 도구 기록 줄 뒤
    assert lines[-2]["event"] in ("plan", "claim")
    assert decisions(log) == []  # 상황 신호가 없으면 결정 로그도 없다


# ── 2. 표시 갱신은 계획 수정 한도를 쓰지 않는다 ─────────────────────────────


def test_display_updates_do_not_use_plan_edits() -> None:
    req = F.with_loaded(F.request(user_message=MSG), "t-4")

    names = ["첫째 확인", "둘째 확인", "셋째 확인", "넷째 확인"]  # 단계 글에는 숫자를 쓰지 않는다

    def edit(n: int) -> Any:
        steps = [
            {"step": names[n], "status": "DOING", "note": None, "tool": None},
            {"step": "답 쓰기", "status": "TODO", "note": None, "tool": "answer"},
        ]
        return F.tools(("update_plan", {"steps": steps}), prefix=f"u{n}")

    reads = [
        COMPARE,
        ("list_takes", {}),
        ("get_take_report", {"take_id": "t-5", "section": "dimensions"}),
    ]
    script: list[Any] = [compare_plan(("기록 확인", None), ("답 쓰기", "answer"))]
    for n in range(4):
        script.append(edit(n))
        if n < 3:
            script.append(F.tools(reads[n], prefix=f"r{n}"))
    script += [F.done(), compare_answer()]
    llm = F.FakeLlm(script)
    _, lines, data = F.run_turn(req, llm)
    fd = check_stream(lines, data)
    limits = [s for s in fd.diagnostics.steps if s.kind == "LIMIT"]  # type: ignore[union-attr]
    # 모델의 update_plan 3번은 받고 4번째만 한도(표시 갱신이 여러 번 있었어도)
    assert [s.code for s in limits] == ["PLAN_EDITS"]
    assert limits[0].detail.used == 3  # type: ignore[union-attr]
    plans = plan_lines(lines)
    shown = [s["step"] for p in plans for s in p]
    # 고친 3번은 목록에 들어가고 4번째는 들어가지 않는다(고친 목록은 실행한 단계 뒤에 붙는다)
    assert all(any(f.startswith(names[n]) for f in shown) for n in range(3))
    assert not any(f.startswith(names[3]) for f in shown)
    assert len(plans) >= 1 + 3 + 3  # 처음 계획 + 고친 3번 + 도구마다 표시 갱신


# ── 3. 비교 불가 → 사실 표시 → 다른 대상 ─────────────────────────────────────


def test_not_comparable_shows_fact_then_alt_tool_appends_step_after_run() -> None:
    req = other_script_take3(F.with_loaded(F.request(user_message=MSG), "t-3", "t-4"))
    compare35 = ("compare_takes", {**COMPARE[1], "previous_take_id": "t-3"})
    llm = F.FakeLlm(
        [
            compare_plan(("반복 확인", "compare_takes"), ("답 쓰기", "answer")),
            F.tools(compare35, prefix="a"),
            F.tools(COMPARE, prefix="b"),
            F.done(),
            compare_answer(),
        ]
    )
    log: list[dict[str, Any]] = []
    _, lines, data = F.run_turn(req, llm, log=log)
    check_stream(lines, data)
    plans = plan_lines(lines)
    assert plans[1][0]["step"] == "반복 확인: 비교 → 비교 불가(대본 버전 다름)"
    assert plans[1][0]["status"] == "DONE"
    assert len(plans[1]) == 2  # 다른 Take 비교는 아직 실행 전이라 보이지 않는다
    # 모델이 다른 Take 비교를 실행한 뒤에만 단계가 붙는다(남은 단계 앞)
    assert len(plans[2]) == 3
    assert plans[2][1]["step"] == "비교: Take 4·5 비교 → 3장 청중 시선 나빠짐"
    assert [p["status"] for p in data["plan"]] == ["DONE", "DONE", "DONE"]
    assert decisions(log) == [
        ("NOT_COMPARABLE", "FACT_SHOWN", "CODE", 1),
        ("NOT_COMPARABLE", "ALT_TOOL", "LLM", 2),
    ]
    # 두 번째 조사 호출의 데이터 블록에 대응을 기다리는 신호가 실렸다
    research = [c for c in llm.calls if c.purpose == "RESEARCH" and c.tools]
    block = json.loads(research[1].messages[1]["content"][len("<data>\n") : -len("\n</data>")])
    assert block["signals"][0]["signal"] == "NOT_COMPARABLE"
    assert block["signals"][0]["facts"] == ["DIFFERENT_SCRIPT_VERSION"]
    assert block["signals"][0]["options"]
    assert prompts.RESEARCH_SIGNALS_HINT in research[1].messages[0]["content"]
    assert "signals" not in research[0].messages[1]["content"]


def test_unanswered_signal_is_closed_with_none() -> None:
    req = other_script_take3(F.with_loaded(F.request(user_message=MSG), "t-3", "t-4"))
    compare35 = ("compare_takes", {**COMPARE[1], "previous_take_id": "t-3"})
    llm = F.FakeLlm(
        [
            compare_plan(("반복 확인", "compare_takes"), ("답 쓰기", "answer")),
            F.tools(compare35),
            F.done(),
            compare_answer(),
        ]
    )
    log: list[dict[str, Any]] = []
    F.run_turn(req, llm, log=log)
    assert decisions(log) == [
        ("NOT_COMPARABLE", "FACT_SHOWN", "CODE", 1),
        ("NOT_COMPARABLE", "NONE", "LLM", None),
    ]


def test_plan_edit_after_signal_is_logged_with_update_plan_seq() -> None:
    req = other_script_take3(F.with_loaded(F.request(user_message=MSG), "t-3", "t-4"))
    compare35 = ("compare_takes", {**COMPARE[1], "previous_take_id": "t-3"})
    steps = [
        {"step": "반복 확인", "status": "DONE", "note": None, "tool": "compare_takes"},
        {"step": "앞 Take 와 비교", "status": "DOING", "note": None, "tool": "compare_takes"},
        {"step": "답 쓰기", "status": "TODO", "note": None, "tool": "answer"},
    ]
    llm = F.FakeLlm(
        [
            compare_plan(("반복 확인", "compare_takes"), ("답 쓰기", "answer")),
            F.tools(compare35, prefix="a"),
            F.tools(("update_plan", {"steps": steps}), COMPARE, prefix="b"),
            F.done(),
            compare_answer(),
        ]
    )
    log: list[dict[str, Any]] = []
    _, lines, data = F.run_turn(req, llm, log=log)
    check_stream(lines, data)
    assert decisions(log) == [
        ("NOT_COMPARABLE", "FACT_SHOWN", "CODE", 1),
        ("NOT_COMPARABLE", "PLAN_EDIT", "LLM", 2),
    ]
    # 고친 목록에서도 실행한 단계는 실행 결과를 이어 받고, 새 단계에 비교가 붙는다
    assert data["plan"][0]["step"] == "반복 확인: 비교 → 비교 불가(대본 버전 다름)"
    assert data["plan"][1]["step"] == "앞 Take 와 비교: Take 4·5 비교 → 3장 청중 시선 나빠짐"
    assert [p["status"] for p in data["plan"]] == ["DONE", "DONE", "DONE"]


# ── 3-1. 실행 뒤 모델이 목록을 다른 글로 고침 → 실행한 단계는 남기고 새 단계를 뒤에 ─────────


def _edit(*names_tools: tuple[str, str | None], prefix: str = "u") -> Any:
    steps = [
        {"step": n, "status": "DOING" if i == 0 else "TODO", "note": None, "tool": t}
        for i, (n, t) in enumerate(names_tools)
    ]
    return F.tools(("update_plan", {"steps": steps}), prefix=prefix)


def _ran_two_then_edit(edit: Any, cfg: Any = None) -> tuple[Any, Any, Any]:
    """비교 · Take 목록 두 단계를 실행한 뒤 모델이 update_plan 으로 목록을 다른 글로 바꾼다. 그 뒤 조사 호출이 받은 도구 결과를 남긴다."""
    req = F.with_loaded(F.request(user_message=MSG), "t-4")
    seen: dict[str, Any] = {}

    def done_after(r: Any) -> Any:
        seen["edit_result"] = F.tool_results(r)[-1]  # update_plan 도구 결과(모델에게 돌려준 것)
        return F.done()

    llm = F.FakeLlm(
        [
            compare_plan(
                ("반복 확인", "compare_takes"), ("목록 보기", "list_takes"), ("답 쓰기", "answer")
            ),
            F.tools(COMPARE, prefix="a"),
            F.tools(("list_takes", {}), prefix="b"),
            edit,
            done_after,
            compare_answer(),
        ]
    )
    _, lines, data = F.run_turn(req, llm, **({"cfg": cfg} if cfg is not None else {}))
    return lines, data, seen


RAN = [
    {"step": "반복 확인: Take 4·5 비교 → 3장 청중 시선 나빠짐", "status": "DONE", "note": None},
    {"step": "목록 보기: Take 목록 → 확인", "status": "DONE", "note": None},
]


def test_plan_edit_after_runs_keeps_executed_steps_in_front_and_appends_new_ones() -> None:
    edit = _edit(
        ("문제 구간 보기", "get_take_report"), ("자료 찾기", "search_guides"), ("답 정리", "answer")
    )
    lines, data, seen = _ran_two_then_edit(edit)
    fd = check_stream(lines, data)
    assert fd.status == "DONE"
    plans = plan_lines(lines)
    # 고친 직후 화면: 실행한 2단계(결과 글 그대로) + 새 3단계
    edited = next(p for p in plans if any(s["step"] == "문제 구간 보기" for s in p))
    assert edited[:2] == RAN
    assert [s["step"] for s in edited[2:]] == ["문제 구간 보기", "자료 찾기", "답 정리"]
    assert [s["status"] for s in edited] == ["DONE", "DONE", "DOING", "TODO", "TODO"]
    # 모델에게 돌려준 update_plan 결과도 합친 목록이다
    assert seen["edit_result"]["ok"] is True and seen["edit_result"]["data"]["plan"] == edited
    # 끝: 실행한 단계는 DONE 그대로, 실행하지 않은 새 단계만 SKIPPED(답 쓰기 단계는 답이 있어 DONE)
    assert data["plan"][:2] == RAN
    assert [s["status"] for s in data["plan"]] == ["DONE", "DONE", "SKIPPED", "SKIPPED", "DONE"]
    assert not [s for s in fd.diagnostics.steps if s.kind == "LIMIT"]  # type: ignore[union-attr]


def test_plan_edit_merge_cuts_new_steps_to_eight() -> None:
    new = [(f"새 단계 {'가나다라마바사'[i]}", None) for i in range(7)]
    lines, data, seen = _ran_two_then_edit(_edit(*new))
    check_stream(lines, data)
    steps = [s["step"] for s in data["plan"]]
    assert len(steps) == 8 and data["plan"][:2] == RAN
    # 남긴 실행 단계는 자르지 않고 뒤의 새 단계(7번째)를 자른다
    assert steps[2:] == [n for n, _ in new[:6]]
    assert len(seen["edit_result"]["data"]["plan"]) == 8


def test_plan_edit_with_plan_progress_off_keeps_the_model_list() -> None:
    edit = _edit(
        ("문제 구간 보기", "get_take_report"), ("자료 찾기", "search_guides"), ("답 정리", "answer")
    )
    lines, data, seen = _ran_two_then_edit(edit, cfg=load_config(plan_progress=False))
    check_stream(
        lines, data, live_is_final=False
    )  # 이전 동작: 끝에 단계를 모두 DONE 으로(줄을 다시 내지 않음)
    assert [s["step"] for s in data["plan"]] == ["문제 구간 보기", "자료 찾기", "답 정리"]
    assert [s["status"] for s in data["plan"]] == ["DONE", "DONE", "DONE"]  # 이전 동작
    assert [s["step"] for s in seen["edit_result"]["data"]["plan"]] == [
        "문제 구간 보기",
        "자료 찾기",
        "답 정리",
    ]


# ── 4. 조사 시간 초과 → PARTIAL ──────────────────────────────────────────────


def test_partial_on_research_time_leaves_todo_and_time_short() -> None:
    clock = F.Clock()

    def compare_then_late(_req: Any) -> Any:
        clock.t += 30  # 다음 조사 호출에 남은 시간이 없다(답 몫 24초는 남음)
        return F.tools(COMPARE)

    req = F.with_loaded(F.request(user_message=MSG), "t-4")
    llm = F.FakeLlm(
        [
            compare_plan(
                ("반복 확인", "compare_takes"),
                ("문제 구간 보기", "get_take_report"),
                ("답 쓰기", "answer"),
            ),
            compare_then_late,
        ],
        answers=[compare_answer()],
    )
    log: list[dict[str, Any]] = []
    _, lines, data = F.run_turn(req, llm, clock=clock, log=log)
    fd = check_stream(lines, data)
    assert fd.status == "PARTIAL"
    assert fd.diagnostics.end.reason == "TIME"  # type: ignore[union-attr]
    assert data["plan"][1] == {"step": "문제 구간 보기: 시간 부족", "status": "TODO", "note": None}
    assert [p["status"] for p in data["plan"]] == ["DONE", "TODO", "DONE"]
    assert ("BUDGET_LOW", "STOP_PARTIAL", "CODE", None) in decisions(log)
    line = next(d for d in log if d.get("signal") == "BUDGET_LOW")
    assert line["facts"] == ["TIME"] and line["step"] == 1


# ── 5. 가져오기 → 이어 부르기 ────────────────────────────────────────────────


def test_needs_action_keeps_plan_meta_across_resume() -> None:
    req = F.request(user_message=MSG)  # Take 4 결과가 기본 자료에 없음
    llm = F.FakeLlm(
        [
            compare_plan(("반복 확인", "compare_takes"), ("답 쓰기", "answer")),
            F.tools(COMPARE),
            F.tools(("fetch_take", {"take_id": "t-4", "reason": "비교할 Take 4"}), prefix="act"),
        ]
    )
    log: list[dict[str, Any]] = []
    _, lines, data = F.run_turn(req, llm, log=log)
    fd = check_stream(lines, data)
    assert fd.status == "NEEDS_ACTION"
    assert data["plan"] == [
        {"step": "반복 확인: 비교 → 기본 자료에 없음", "status": "DONE", "note": None},
        {"step": "답 쓰기", "status": "DOING", "note": None},
    ]
    assert decisions(log) == [
        ("NOT_LOADED", "FACT_SHOWN", "CODE", 1),
        ("NOT_LOADED", "ACTION", "LLM", None),
    ]
    action = fd.actions[0]
    res = [
        {
            "action_id": action.action_id,
            "type": "FETCH_TAKE",
            "status": "SUCCEEDED",
            "data": {"take": fetched_take("t-4")},
            "error": None,
        }
    ]
    log2: list[dict[str, Any]] = []
    llm2 = F.FakeLlm([F.tools(COMPARE, prefix="c2"), F.done(), compare_answer()])
    _, lines2, data2 = F.run_turn(resume_request(req, data, res), llm2, log=log2)
    check_stream(lines2, data2)
    # 서명 상태로 이어진 단계 메타: 앞 호출의 실행 결과가 남고, 가져오기 · 다시 비교가 단계로 붙는다
    assert [p["step"] for p in data2["plan"]] == [
        "반복 확인: 비교 → 기본 자료에 없음",
        "기록 가져오기 → 가져옴",
        "비교: Take 4·5 비교 → 3장 청중 시선 나빠짐",
        "답 쓰기",
    ]
    assert [p["status"] for p in data2["plan"]] == ["DONE"] * 4
    assert all(d["turn_call"] == 2 for d in log2 if d.get("event") == "agent_decision")


@pytest.mark.prefetch
def test_code_prefetch_fetch_take_is_logged_as_code_action() -> None:
    req = F.request(user_message=MSG)  # Take 4 결과가 기본 자료에 없음 → 코드가 먼저 가져온다
    llm = F.FakeLlm([compare_plan(("반복 확인", "compare_takes"), ("답 쓰기", "answer"))])
    log: list[dict[str, Any]] = []
    _, lines, data = F.run_turn(req, llm, log=log)
    fd = check_stream(lines, data)
    assert fd.status == "NEEDS_ACTION" and fd.actions[0].type == "FETCH_TAKE"
    assert decisions(log) == [("NOT_LOADED", "ACTION", "CODE", None)]


# ── 6 · 7. 단계 근거 밖 숫자 · 결정 로그 모양 ─────────────────────────────────


def test_step_text_takes_numbers_only_from_its_own_evidence() -> None:
    req = F.with_loaded(F.request(user_message=MSG), "t-4")
    for snap in req["context"]["loaded_takes"]:
        snap["results"]["review"] = None  # Take 4 리뷰 없음
    llm = F.FakeLlm(
        [
            compare_plan(
                ("반복 확인", "compare_takes"),
                ("리뷰 보기", "get_take_report"),
                ("답 쓰기", "answer"),
            ),
            F.tools(COMPARE, ("get_take_report", {"take_id": "t-4", "section": "dimensions"})),
            F.done(),
            compare_answer(),
        ]
    )
    log: list[dict[str, Any]] = []
    _, lines, data = F.run_turn(req, llm, log=log)
    check_stream(lines, data)
    assert data["plan"][0]["step"].startswith("반복 확인: Take 4·5 비교")
    # 리뷰 단계의 'Take 4'는 그 단계가 낸 리뷰 상태 근거(review.status NONE, '없음'도 근거)로 받쳐진다.
    # 근거 없는 번호를 빼는 규칙은 test_progress.test_display_text_drops_numbers_without_step_evidence
    assert data["plan"][1]["step"] == "리뷰 보기: Take 4 기록 → 리뷰 분석 전"
    assert ("REVIEW_MISSING", "FACT_SHOWN", "CODE", 2) in decisions(log)
    # 결정 로그 줄 모양: 키 고정, 값은 enum · 정수 · null 뿐(사용자 글 · 숫자 값 없음)
    lines_ = [d for d in log if d.get("event") == "agent_decision"]
    assert lines_
    for d in lines_:
        assert tuple(d) == progress.DECISION_KEYS
        for k in ("signal", "decider", "effect"):
            assert _ENUM.match(d[k])
        assert all(_ENUM.match(f) for f in d["facts"])
        for k in ("turn_call", "seq"):
            assert isinstance(d[k], int)
        for k in ("step", "tool_seq"):
            assert d[k] is None or isinstance(d[k], int)
        blob = json.dumps(d, ensure_ascii=False)
        assert MSG not in blob and not re.search(r"[가-힣]", blob)
    assert [d["seq"] for d in lines_] == list(range(1, len(lines_) + 1))
    # 진단 기록(BE 로 감)에는 결정 로그가 없다
    diag = json.dumps(data["diagnostics"], ensure_ascii=False)
    assert "agent_decision" not in diag and "FACT_SHOWN" not in diag


# ── 9. 스위치를 모두 끄면 이전 동작 ─────────────────────────────────────────


def test_switches_off_keep_previous_behavior() -> None:
    cfg = load_config(plan_progress=False, decision_log=False, research_signals=False)
    req = other_script_take3(F.with_loaded(F.request(user_message=MSG), "t-3", "t-4"))
    compare35 = ("compare_takes", {**COMPARE[1], "previous_take_id": "t-3"})
    llm = F.FakeLlm(
        [
            compare_plan(
                ("반복 확인", "compare_takes"), ("자료 찾기", None), ("답 쓰기", "answer")
            ),
            F.tools(compare35, prefix="a"),
            F.tools(COMPARE, prefix="b"),
            F.done(),
            compare_answer(),
        ]
    )
    log: list[dict[str, Any]] = []
    _, lines, data = F.run_turn(req, llm, cfg=cfg, log=log)
    fd = check_stream(lines, data, live_is_final=False)
    n = len(fd.message.claims)  # type: ignore[union-attr]
    assert [ln["event"] for ln in lines] == [
        "plan",
        "tool_done",
        "tool_done",
        *["claim"] * n,
        "final",
    ]
    # 끝에 단계를 모두 DONE 으로(이전 동작), 단계 글은 모델 글 그대로
    assert data["plan"] == [
        {"step": "반복 확인", "status": "DONE", "note": None},
        {"step": "자료 찾기", "status": "DONE", "note": None},
        {"step": "답 쓰기", "status": "DONE", "note": None},
    ]
    assert decisions(log) == []
    research = [c for c in llm.calls if c.purpose == "RESEARCH" and c.tools]
    assert all(prompts.RESEARCH_SIGNALS_HINT not in c.messages[0]["content"] for c in research)
    assert all('"signals"' not in c.messages[1]["content"] for c in research)


# ── 10. trace 출처 ──────────────────────────────────────────────────────────


def test_take_report_trace_keeps_its_source_in_live_and_final_trace() -> None:
    req = F.with_loaded(F.request(user_message=MSG), "t-4")
    reads = (
        ("get_take_report", {"take_id": "t-5", "section": "dimensions"}),
        ("get_take_report", {"take_id": "t-5", "section": "problem_segments"}),
    )
    llm = F.FakeLlm(
        [
            compare_plan(("반복 확인", "compare_takes"), ("답 쓰기", "answer")),
            F.tools(COMPARE, *reads),
            F.done(),
            compare_answer(),
        ]
    )
    _, lines, data = F.run_turn(req, llm)
    check_stream(lines, data)
    live = [ln["data"]["trace"]["result_summary"] for ln in lines if ln["event"] == "tool_done"]
    assert live[1].startswith("리뷰 분석 · ") and live[2].startswith("코치가 찾은 것 · ")
    # 끝 줄의 trace 는 표시 검사로 숫자를 줄여도 읽은 출처는 남긴다
    final = [t["result_summary"] for t in data["trace"]]
    assert final[1].startswith("리뷰 분석 · ") and final[2].startswith("코치가 찾은 것 · ")
    assert [p["step"] for p in data["plan"][1:3]] == [
        "기록 보기: Take 5 기록 → 리뷰 우선 개선 청중 시선",
        "기록 보기: Take 5 기록 → 문제 구간 있음",
    ]


# ── TARGET 1절 예의 꼴 · 목적의 숫자 ─────────────────────────────────────────


def test_step_text_matches_target_examples() -> None:
    """TARGET 1절 예 `리뷰 보기: Take 5 기록 → 리뷰 우선 개선 청중 시선` · `반복 확인: Take 3·4 비교 → 3장 시선 나빠짐`의 꼴.
    지표 표시 이름은 기존 이름(`청중 시선`)이라 둘째 예는 `3장 청중 시선 나빠짐`이다."""
    req = F.with_loaded(F.request(user_message=MSG), "t-3", "t-4")
    compare34 = (
        "compare_takes",
        {**COMPARE[1], "previous_take_id": "t-3", "current_take_id": "t-4"},
    )
    llm = F.FakeLlm(
        [
            compare_plan(
                ("반복 확인", "compare_takes"),
                ("리뷰 보기", "get_take_report"),
                ("답 쓰기", "answer"),
            ),
            F.tools(compare34, ("get_take_report", {"take_id": "t-5", "section": "dimensions"})),
            F.done(),
            compare_answer(record=False),
        ]
    )
    _, lines, data = F.run_turn(req, llm)
    check_stream(lines, data)
    assert [p["step"] for p in data["plan"]] == [
        "반복 확인: Take 3·4 비교 → 3장 청중 시선 나빠짐",
        "리뷰 보기: Take 5 기록 → 리뷰 우선 개선 청중 시선",
        "답 쓰기",
    ]


def test_number_in_purpose_is_dropped_not_whole_plan() -> None:
    """모델이 목적에 '3장'을 써도 기본 계획으로 바꾸지 않고 그 표기만 지운다. 숫자는 실행 뒤 근거로 받쳐 결과 쪽에 붙는다."""
    req = F.with_loaded(F.request(user_message=MSG), "t-4")
    llm = F.FakeLlm(
        [
            compare_plan(("3장 시선 확인", "compare_takes"), ("답 쓰기", "answer")),
            F.tools(COMPARE),
            F.done(),
            compare_answer(),
        ]
    )
    _, lines, data = F.run_turn(req, llm)
    check_stream(lines, data)
    assert [p["step"] for p in plan_lines(lines)[0]] == ["시선 확인", "답 쓰기"]
    assert data["plan"][0]["step"] == "시선 확인: Take 4·5 비교 → 3장 청중 시선 나빠짐"
    # 숫자만 있던 목적은 그 단계 도구의 종류 이름으로
    llm2 = F.FakeLlm(
        [
            compare_plan(("Take 4·5", "compare_takes"), ("답 쓰기", "answer")),
            F.tools(COMPARE),
            F.done(),
            compare_answer(),
        ]
    )
    _, lines2, _ = F.run_turn(req, llm2)
    assert [p["step"] for p in plan_lines(lines2)[0]] == ["비교", "답 쓰기"]
