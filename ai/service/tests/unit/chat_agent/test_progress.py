"""할 일 단계 = 실제 실행 단위 시험 — 단계 초안 · 단계 고르기 · 표시 글 · 끝 처리 · 상황 신호 · trace 출처 (progress.py, 기능 문서 6-1 7번).

도구는 예시 요청(범위 p-1 · 보던 Take t-5)과 가상 기록(fixtures.py)으로 실제로 돌린다(test_tools.py 의 도우미).
"""

from __future__ import annotations

import copy
from string import Formatter
from typing import Any

import pytest

from pitch_coach_ai.features.chat_agent import progress
from pitch_coach_ai.features.chat_agent import templates as tpl
from pitch_coach_ai.features.chat_agent.claims import DIM_LABEL, METRIC_LABEL
from pitch_coach_ai.features.chat_agent.config import DEFAULT_CONFIG as CFG
from pitch_coach_ai.features.chat_agent.schemas import PlanStep

from .test_tools import base_request, load, make_ctx, run

COMPARE = {
    "previous_take_id": "t-4",
    "current_take_id": "t-5",
    "metric": "audience_ratio",
    "slide_number": 3,
}


def _meta(*steps: tuple[str, str | None]) -> list[dict[str, Any]]:
    plan = [
        {"step": s, "status": "DOING" if i == 0 else "TODO", "note": None, "tool": t}
        for i, (s, t) in enumerate(steps)
    ]
    rows, hints = progress.split_steps(plan)
    return progress.new_meta(rows, hints)


def _read(ctx: Any, name: str, args: dict[str, Any]) -> progress.StepRun:
    from pitch_coach_ai.features.chat_agent.tools import validate_args

    a = validate_args(name, args)
    assert not isinstance(a, str)
    tr, _ = run(ctx, name, args)
    return progress.read_run(name, a, tr, ctx)


# ── 단계 초안(PlanStepDraft) ────────────────────────────────────────────────


def test_draft_tool_hint_is_stripped_from_the_response_step() -> None:
    rows, hints = progress.split_steps(
        [
            {"step": "반복 확인", "status": "DOING", "note": None, "tool": "compare_takes"},
            {"step": "답 쓰기", "status": "TODO", "note": None, "tool": "모르는 도구"},
            {"step": "끝", "status": "TODO", "note": None},
        ]
    )
    assert hints == ["compare_takes", None, None]  # 모르는 힌트는 없는 것으로
    for r in rows:
        assert set(r) == {"step", "status", "note"}
        PlanStep.model_validate(r)


# ── 단계 고르기 ──────────────────────────────────────────────────────────────


def test_pick_prefers_matching_hint_then_hintless_current_step() -> None:
    meta = _meta(("기록 확인", None), ("비교", "compare_takes"), ("답 쓰기", "answer"))
    assert progress.pick(meta, "compare_takes") == 1
    assert progress.pick(meta, "list_takes") == 0  # 힌트 없는 지금 단계
    meta[0]["done"] = True
    assert progress.pick(meta, "list_takes") is None  # 지금 단계(비교)는 힌트가 있다


def test_attach_inserts_new_step_before_pending_steps_up_to_eight() -> None:
    ctx = make_ctx()
    meta = _meta(("반복 확인", "compare_takes"), ("답 쓰기", "answer"))
    i = progress.attach(meta, _read(ctx, "compare_takes", COMPARE), 1, max_steps=8)
    assert i == 0 and meta[0]["done"]
    j = progress.attach(meta, _read(ctx, "list_takes", {}), 2, max_steps=8)
    # 계획에 없던 도구는 실행 뒤에 '도구 종류 이름' 단계로 남은 단계(답 쓰기) 앞에 붙는다
    assert j == 1 and [m["purpose"] for m in meta] == ["반복 확인", "Take 목록", "답 쓰기"]
    full = _meta(*[(f"단계 {n}", "answer") for n in range(8)])
    assert progress.attach(full, _read(ctx, "list_takes", {}), 3, max_steps=8) is None
    assert len(full) == 8  # 8단계면 붙이지 않는다(trace 에만 남는다)


# ── 표시 글 ──────────────────────────────────────────────────────────────────


def test_display_text_is_purpose_exec_result_within_sixty_chars() -> None:
    ctx = make_ctx()
    meta = _meta(("반복 확인", "compare_takes"))
    progress.attach(meta, _read(ctx, "compare_takes", COMPARE), 1, max_steps=8)
    plan = progress.render(meta, ctx.registry, CFG)
    assert plan[0] == {
        "step": "반복 확인: Take 4·5 비교 → 3장 청중 시선 나빠짐",
        "status": "DONE",
        "note": None,
    }
    long = "아주 긴 목적" * 10
    text = progress.compose(long, "Take 4·5 비교", "3장 청중 시선 나빠짐")
    assert len(text) <= 60 and text.endswith("…: Take 4·5 비교 → 3장 청중 시선 나빠짐")


def test_step_text_result_is_never_cut_silently() -> None:
    """실행 · 결과 글은 템플릿 표로만 만든다: 세 자리 Take · 장 번호로 잡은 가장 긴 `실행 → 결과`도 60자 안이다. 번호 자릿수에는
    상한이 없어 넘칠 수 있으므로, 넘치면 끝을 줄여 "…"를 붙인다 — 잘린 글이 결과 전체로 읽히지 않게(#1163 리뷰)."""
    n = "999"
    execs = [
        f"Take {n}·{n} 비교",
        tpl.PLAN_REPORT_EXEC.format(n=n),
        f"Take {n} 가져오기",
        *tpl.PLAN_EXEC.values(),
        *tpl.PLAN_TOOL_NAME.values(),
    ]
    reasons = [*tpl.PLAN_NOT_COMPARABLE.values(), tpl.PLAN_NOT_COMPARABLE_OTHER]
    slots = {"reason": reasons, "dimension": list(DIM_LABEL.values()), "slide": [n]}
    results = []
    for text in tpl.PLAN_RESULT.values():
        fields = [f for _, f, _, _ in Formatter().parse(text) if f]
        # 새 자리가 생기면 여기서 KeyError: 그 자리에 들어갈 가장 긴 값을 slots 에 더한다
        results += [text.format(**{f: v}) for f in fields for v in slots[f]] or [text]
    outcomes = [*tpl.PLAN_OUTCOME.values(), tpl.PLAN_OUTCOME_OTHER]
    results += [f"{n}장 {m} {o}" for m in [*METRIC_LABEL.values(), "지표"] for o in outcomes]
    longest = f"{max(execs, key=len)} → {max(results, key=len)}"
    assert len(longest) < progress.STEP_CHARS, longest

    cut = progress.compose("짧음", "x" * 70, "y")
    assert len(cut) == 60 and cut.endswith("x…")
    # 아주 긴 Take 번호 둘의 비교(번호 자릿수 상한 없음): 결과가 '…' 없이 끊기지 않는다
    big = "9" * 21
    text = progress.compose(
        "반복 확인", f"Take {big}·{big} 비교", "999장 핵심 사실 정확도 판단 불가"
    )
    assert len(text) <= 60 and text.endswith("장…")
    # 덧붙은 단계(실행 = 목적)도 같다
    same = progress.compose("x" * 70, "x" * 70, "y")
    assert len(same) == 60 and same.endswith("…")
    assert progress.compose("기록 보기", "기록 보기", "확인") == "기록 보기 → 확인"


def test_display_text_drops_numbers_without_step_evidence() -> None:
    """숫자 · Take · 장 번호는 그 단계 근거로만: 근거가 없으면 숫자 없는 실행 이름으로 그린다."""
    req = load(base_request(), "t-4")  # Take 3 은 기본 자료에 없다(요약만)
    ctx = make_ctx(req)
    meta = _meta(("반복 확인", "compare_takes"), ("이전 기록 보기", "get_take_report"))
    progress.attach(meta, _read(ctx, "compare_takes", COMPARE), 1, max_steps=8)
    run3 = _read(ctx, "get_take_report", {"take_id": "t-3", "section": "dimensions"})
    assert run3.exec == "Take 3 기록" and run3.evidence_ids == []
    progress.attach(meta, run3, 2, max_steps=8)
    plan = progress.render(meta, ctx.registry, CFG)
    assert plan[0]["step"].startswith("반복 확인: Take 4·5 비교")
    # 'Take 3'을 받칠 근거가 이 단계에 없다 → 숫자 없는 실행 이름
    assert plan[1]["step"] == "이전 기록 보기: 기록 보기 → 기본 자료에 없음"


def test_review_missing_step_is_backed_by_its_own_none_evidence() -> None:
    """리뷰 없음은 그 Take 의 리뷰 상태 근거(`review.status` NONE)를 낸다: 단계 글의 'Take 4'는 그 단계 자기 근거로 받쳐진다."""
    req = load(base_request(), "t-4")
    for snap in req["context"]["loaded_takes"]:
        snap["results"]["review"] = None
    ctx = make_ctx(req)
    meta = _meta(
        ("리뷰 보기", "get_take_report"),
    )
    run4 = _read(ctx, "get_take_report", {"take_id": "t-4", "section": "dimensions"})
    assert run4.signal == "REVIEW_MISSING" and len(run4.evidence_ids) == 1
    ev = ctx.registry.get(run4.evidence_ids[0])
    assert (ev.path, ev.value, ev.take_number) == ("review.status", "NONE", 4)
    progress.attach(meta, run4, 1, max_steps=8)
    assert (
        progress.render(meta, ctx.registry, CFG)[0]["step"]
        == "리뷰 보기: Take 4 기록 → 리뷰 분석 전"
    )


# ── 결과 글 · 상황 신호 ──────────────────────────────────────────────────────


def test_read_run_results_and_signals() -> None:
    req = load(base_request(), "t-4", "t-3")
    for t in req["context"]["pitch"]["takes"]:
        if t["take_id"] == "t-3":
            t["script_version_id"] = "sv-0"
    for snap in req["context"]["loaded_takes"]:
        if snap["summary"]["take_id"] == "t-3":
            snap["summary"]["script_version_id"] = "sv-0"
            snap["results"]["take_result"]["problem_segments"] = []
    ctx = make_ctx(req)
    nc = _read(ctx, "compare_takes", {**COMPARE, "previous_take_id": "t-3"})
    assert (nc.result, nc.done, nc.signal, nc.facts) == (
        "비교 불가(대본 버전 다름)",
        True,
        "NOT_COMPARABLE",
        ["DIFFERENT_SCRIPT_VERSION"],
    )
    seg = _read(ctx, "get_take_report", {"take_id": "t-3", "section": "problem_segments"})
    assert (seg.result, seg.signal, seg.facts) == (
        "문제 구간 없음",
        "NO_RECORD",
        ["PROBLEM_SEGMENTS"],
    )
    have = _read(ctx, "get_take_report", {"take_id": "t-5", "section": "problem_segments"})
    assert (have.exec, have.result, have.signal) == ("Take 5 기록", "문제 구간 있음", None)
    dims = _read(ctx, "get_take_report", {"take_id": "t-5", "section": "dimensions"})
    assert (dims.exec, dims.result) == ("Take 5 기록", "리뷰 우선 개선 청중 시선")
    goals = _read(ctx, "get_goals", {})
    assert (goals.result, goals.signal, goals.facts) == ("목표 없음", "NO_RECORD", ["GOAL"])
    missing = _read(ctx, "get_take_report", {"take_id": "t-9", "section": "slides"})
    assert (missing.result, missing.done, missing.signal) == ("확인 실패", False, "TOOL_ERROR")


def test_read_run_not_loaded_and_empty_takes() -> None:
    ctx = make_ctx(base_request())  # Take 4 결과가 기본 자료에 없음
    nl = _read(ctx, "compare_takes", COMPARE)
    assert (nl.result, nl.done, nl.signal) == ("기본 자료에 없음", True, "NOT_LOADED")
    req = base_request()
    req["context"]["pitch"]["takes"] = []
    req["context"]["pitch"]["focus_take"] = None
    req["page"]["take_id"] = None
    empty = _read(make_ctx(req), "list_takes", {})
    assert (empty.result, empty.signal, empty.facts) == ("Take 없음", "NO_RECORD", ["TAKES"])


def test_not_loaded_without_scope_pitch_shows_fact_but_opens_no_signal() -> None:
    """r9 회귀(glob-ambiguous-name · glob-weakest): 전체 범위에서 list_takes 가 막힌 것(범위 피치 없음)에 NOT_LOADED 신호(가져오기
    대응)를 내면 모델이 fetch_pitch 로 범위를 바꿨다. 단계에는 사실(`기본 자료에 없음`)만 보이고 신호는 내지 않는다."""
    nl = _read(make_ctx(scope=None), "list_takes", {})
    assert (nl.result, nl.done, nl.signal) == ("기본 자료에 없음", True, None)
    scoped = _read(make_ctx(base_request()), "compare_takes", COMPARE)
    assert scoped.signal == "NOT_LOADED"  # 범위 피치가 있으면 그대로(가져올 Take 가 있다)


def test_unmeasured_segments_are_not_no_segments() -> None:
    req = load(base_request(), "t-3")
    tr = req["context"]["loaded_takes"][0]["results"]["take_result"]
    tr["problem_segments"] = []
    for area in tr["areas"].values():
        area["take"], area["unmeasured_reason"] = None, "NO_SIGNAL"
    run3 = _read(
        make_ctx(req), "get_take_report", {"take_id": "t-3", "section": "problem_segments"}
    )
    assert (run3.result, run3.signal) == ("측정 안 됨", None)


def test_slides_read_shows_unmeasured_when_the_asked_area_is_unmeasured() -> None:
    """장별 값(slides)을 읽을 때 다른 지표가 측정됐어도 물은 영역(목소리 크기)이 측정 안 됨이면 단계에 `측정 안 됨`을 보인다
    (final60 ana-webinar-voice-unmeasured). 물은 영역을 모르거나 측정됐으면 `확인`."""
    from pitch_coach_ai.features.chat_agent.tools import validate_args

    req = load(base_request(), "t-3")
    vol = req["context"]["loaded_takes"][0]["results"]["take_result"]["areas"]["VOLUME"]
    vol["take"], vol["unmeasured_reason"] = None, "NO_CALIBRATION"
    ctx = make_ctx(req)
    args = {"take_id": "t-3", "section": "slides"}
    a = validate_args("get_take_report", args)
    assert not isinstance(a, str)
    tr, _ = run(ctx, "get_take_report", args)
    asked = progress.read_run("get_take_report", a, tr, ctx, areas=frozenset({"VOLUME"}))
    assert asked.result == "측정 안 됨"
    other = progress.read_run("get_take_report", a, tr, ctx, areas=frozenset({"GAZE"}))
    unknown = progress.read_run("get_take_report", a, tr, ctx)
    assert other.result != "측정 안 됨" and unknown.result != "측정 안 됨"


# ── 끝 처리 ──────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("status", "answered", "expected"),
    [
        ("DONE", True, ["DONE", "SKIPPED", "DONE"]),
        ("REFUSED", False, ["DONE", "SKIPPED", "SKIPPED"]),
        ("PARTIAL", True, ["DONE", "TODO", "DONE"]),
        ("PARTIAL", False, ["DONE", "TODO", "TODO"]),
        ("NEEDS_ACTION", True, ["DONE", "DOING", "TODO"]),
    ],
)
def test_finish_plan_by_end_status(status: str, answered: bool, expected: list[str]) -> None:
    ctx = make_ctx()
    meta = _meta(
        ("반복 확인", "compare_takes"), ("자료 찾기", "search_guides"), ("답 쓰기", "answer")
    )
    progress.attach(meta, _read(ctx, "compare_takes", COMPARE), 1, max_steps=8)
    plan = progress.finish_plan(meta, ctx.registry, CFG, status, answered=answered)
    assert [p["status"] for p in plan] == expected


def test_mark_short_puts_time_short_on_current_step() -> None:
    meta = _meta(("반복 확인", "compare_takes"), ("답 쓰기", "answer"))
    assert progress.mark_short(meta) == 0
    plan = progress.render(meta, make_ctx().registry, CFG)
    assert plan[0]["step"] == "반복 확인: 시간 부족" and plan[0]["status"] == "DOING"


def test_new_meta_keeps_executed_step_when_model_edits_plan() -> None:
    ctx = make_ctx()
    meta = _meta(("반복 확인", "compare_takes"), ("답 쓰기", "answer"))
    progress.attach(meta, _read(ctx, "compare_takes", COMPARE), 1, max_steps=8)
    shown = progress.render(meta, ctx.registry, CFG)
    edit = [
        {"step": "반복 확인", "status": "DONE", "note": None, "tool": "compare_takes"},
        {"step": "문제 구간 보기", "status": "DOING", "note": None, "tool": "get_take_report"},
        {"step": "답 쓰기", "status": "TODO", "note": None, "tool": "answer"},
    ]
    rows, hints = progress.split_steps(edit)
    new = progress.new_meta(rows, hints, old=meta, shown=shown)
    plan = progress.render(new, ctx.registry, CFG)
    assert plan[0] == shown[0]  # 실행한 단계는 그 실행 메타를 이어 받는다
    assert [p["status"] for p in plan] == ["DONE", "DOING", "TODO"]


# ── trace 출처 (D3) ──────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("section", "tag"),
    [
        ("slides", "코치가 찾은 것 · "),
        ("problem_segments", "코치가 찾은 것 · "),
        ("dimensions", "리뷰 분석 · "),
        ("improvements", "리뷰 분석 · "),
        ("mission_results", "리뷰 분석 · "),
    ],
)
def test_take_report_trace_names_its_source(section: str, tag: str) -> None:
    res, trace = run(make_ctx(), "get_take_report", {"take_id": "t-5", "section": section})
    assert res.result_summary.startswith(tag) and trace.result_summary.startswith(tag)
    assert len(trace.result_summary) <= 200
    assert trace.args_summary.startswith("Take 5 · ")  # 인자 요약은 그대로


def test_decision_line_has_fixed_keys() -> None:
    line = progress.decision_line(
        request_id="r",
        run_id="run",
        turn_call=1,
        seq=1,
        signal="NOT_COMPARABLE",
        facts=["DIFFERENT_SCRIPT_VERSION"],
        decider="CODE",
        effect="FACT_SHOWN",
        step=0,
        tool_seq=1,
    )
    assert tuple(line) == progress.DECISION_KEYS
    assert copy.deepcopy(line)["event"] == "agent_decision"


def test_cached_repeat_of_a_shown_call_adds_no_step() -> None:
    """같은 호출을 다시 읽은 캐시 적중은 새 단계를 붙이지 않고 그 호출을 보인 단계를 가리킨다(되풀이 읽기는 새 일이 아니다)."""
    ctx = make_ctx()
    meta = _meta(("비교", "compare_takes"), ("답 쓰기", "answer"))
    key = progress.call_key("compare_takes", COMPARE)
    i = progress.attach(meta, _read(ctx, "compare_takes", COMPARE), 1, max_steps=8, key=key)
    again = progress.attach(
        meta, _read(ctx, "compare_takes", COMPARE), 2, max_steps=8, key=key, cached=True
    )
    assert again == i and len(meta) == 2 and meta[i]["tool_seqs"] == [1]
    # 힌트가 맞는 미완 단계가 있으면 캐시 적중이어도 그 단계를 채운다(모델이 고친 계획의 단계)
    meta2 = _meta(("비교", "compare_takes"), ("다시 비교", "compare_takes"), ("답 쓰기", "answer"))
    progress.attach(meta2, _read(ctx, "compare_takes", COMPARE), 1, max_steps=8, key=key)
    j = progress.attach(
        meta2, _read(ctx, "compare_takes", COMPARE), 2, max_steps=8, key=key, cached=True
    )
    assert j == 1 and meta2[1]["done"] and len(meta2) == 3
    # 처음 보는 호출의 캐시 적중은 보통 실행처럼 붙는다
    other = progress.attach(
        meta, _read(ctx, "list_takes", {}), 3, max_steps=8, key="other", cached=True
    )
    assert other is not None and len(meta) == 3


def test_slide_read_result_names_the_slide() -> None:
    """장을 읽은 단계는 결과에 무엇을 봤는지 보인다(`3장 확인`)."""
    ctx = make_ctx()
    r = _read(
        ctx,
        "get_take_report",
        {"take_id": "t-5", "section": "slides", "slide_number": 3},
    )
    assert r.result == "3장 확인"
