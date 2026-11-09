"""'없음'도 확인한 사실이다 — 근거 기반 답 · 끝 상태 · 결과 발견 안내 시험 (기능 문서 6-8 · 6-9 '고친 뒤' · 7-10, 목표 동작 3 · 6절).

- 도구: 실제로 읽은 범위의 '없음 · 0'(피치별 Take 수 · 범위 피치의 Take 0개 · 리뷰 없음 · 측정된 영역의 문제 구간 0곳 · 대화 목표 없음)을
  같은 `Evidence` 모양(RECORD)으로 낸다. 측정 안 됨 · 이름 검색 0개는 각자의 사실이다(넓히지 않는다).
- 턴: 가짜 모델(fakes.py)로 사용자 시험(Take 가 없는 피치에서 "가장 최근에 한 발표를 보고 싶어")을 재현하고, 끝 상태(DONE · PARTIAL) ·
  코드 대체 문장 · 다음 걸음 · 일반 안내("답 일부를 확인하지 못해 …")가 나오는 조건과 목표 저장 뒤 결과 발견 안내를 본다.
"""

from __future__ import annotations

import copy
import json
from typing import Any

from pitch_coach_ai.features.chat_agent import checks as chk
from pitch_coach_ai.features.chat_agent import compose, progress, prompts, templates
from pitch_coach_ai.features.chat_agent.config import DEFAULT_CONFIG as CFG
from pitch_coach_ai.features.chat_agent.config import load_config
from pitch_coach_ai.features.chat_agent.llm_types import LlmTimeout
from pitch_coach_ai.features.chat_agent.schemas import Claim, Evidence, FinalData
from pitch_coach_ai.features.chat_agent.tools import validate_args

from . import fakes as F
from .fixtures import take_result
from .test_approval import pause, saved_result
from .test_plan_progress_turn import check_stream, decisions
from .test_tools import base_request, load, make_ctx, run
from .test_turn import fetched_take, final_ok, resume_request

T = templates.SystemText
LATEST = "가장 최근에 한 발표를 보고 싶어"
NO_TAKE_SENTENCE = "'졸업 발표'에는 아직 연습한 Take가 없어요."


def no_take_request(message: str = LATEST) -> dict[str, Any]:
    """사용자 시험 화면: 범위 피치(졸업 발표) 하나, Take 0개, 피치 화면에서 연 대화."""
    req = F.request(user_message=message)
    req["page"] = {"route": "/pitches/p-1", "pitch_id": "p-1", "take_id": None, "screen": None}
    req["context"]["pitches"][0].update(take_count=0, last_practiced_at=None, last_review=None)
    req["context"]["pitch"]["takes"] = []
    req["context"]["pitch"]["focus_take"] = None
    req["context"]["loaded_takes"] = []
    return req


def global_request(message: str) -> dict[str, Any]:
    """범위 피치가 없는 대화(전체 범위)."""
    req = F.request(user_message=message)
    req["run"]["scope_pitch_id"] = None
    req["context"]["pitch"] = None
    req["context"]["loaded_takes"] = []
    req["page"] = {"route": "/", "pitch_id": None, "take_id": None, "screen": None}
    return req


def texts(data: dict[str, Any]) -> list[str]:
    return [c["text"] for c in (data.get("message") or {}).get("claims") or []]


def none_ev(r: Any, value: Any = 0) -> str:
    """도구 결과에서 '없음' 근거(값 0 · NONE)의 근거 id."""
    return F.find_evidence(r, lambda o: o.get("value") == value)


def _read(ctx: Any, name: str, args: dict[str, Any], **kw: Any) -> progress.StepRun:
    a = validate_args(name, args)
    assert not isinstance(a, str)
    tr, _ = run(ctx, name, args)
    return progress.read_run(name, a, tr, ctx, **kw)


def with_take_result(req: dict[str, Any], **kw: Any) -> dict[str, Any]:
    """보던 Take 5 의 코치 결과를 바꾼다(문제 구간 · 측정 안 된 영역)."""
    req["context"]["pitch"]["focus_take"]["results"]["take_result"] = take_result(
        take_id="t-5", **kw
    )
    return req


# ── 1. 도구가 내는 '없음' 근거 ────────────────────────────────────────────────


def test_list_pitches_take_count_zero_is_record_evidence_bound_to_the_pitch() -> None:
    ctx = make_ctx(no_take_request())
    res, _ = run(ctx, "list_pitches", {})
    row = res.data["items"][0]
    ev = ctx.registry.get(row["take_count"]["evidence_id"])
    assert row["take_count"]["value"] == 0
    assert (ev.origin, ev.pitch_id, ev.take_id, ev.path, ev.value, ev.unit) == (
        "RECORD",
        "p-1",
        None,
        "pitches[pitch_id=p-1].take_count",
        0,
        "count",
    )
    Evidence.model_validate(ev.model_dump())  # 모양 그대로(5-2)
    assert compose.none_kind(ev) == "TAKES"
    assert compose.none_sentence(ctx, ev) == NO_TAKE_SENTENCE
    # 원천 결속: 피치가 지워지면(이어 부르기의 removed_sources) 이 근거 · 캐시를 함께 버린다(5-6)
    assert "p-1" in res.target_ids
    assert ev.evidence_id in ctx.registry.drop_sources({"p-1"})
    assert ctx.registry.get(ev.evidence_id) is None


def test_list_pitches_name_search_zero_is_the_searched_list_not_all_pitches() -> None:
    ctx = make_ctx(global_request("'창업 경진대회' 발표 기록 보여 줘"), scope=None)
    res, trace = run(ctx, "list_pitches", {"query": "창업 경진대회"})
    assert res.data["items"] == [] and res.data["total"] == 1
    ev = ctx.registry.get(res.data["found"]["evidence_id"])
    assert (ev.path, ev.value, ev.pitch_id) == ("pitches[query].count", 0, None)
    assert (
        "창업" not in ev.path + trace.args_summary + trace.result_summary
    )  # 검색어는 남기지 않는다
    # 이름 검색 0개는 '피치가 없다'로 그리지 않는다(고정 문장 PITCH_NOT_FOUND 길)
    assert compose.none_kind(ev) == "PITCH_QUERY" and compose.none_sentence(ctx, ev) is None


def test_list_takes_zero_names_scope_and_filter_in_the_path() -> None:
    ctx = make_ctx(no_take_request())
    res, _ = run(ctx, "list_takes", {})
    ev = ctx.registry.get(res.data["count"]["evidence_id"])
    assert (ev.path, ev.value, ev.pitch_id) == ("takes[pitch_id=p-1].count", 0, "p-1")
    assert res.target_ids == ["p-1"] and compose.none_kind(ev) == "TAKES"
    # 거른 조건이 있으면 경로에 조건을 남기고, 그 0개를 '피치에 Take 없음'으로 넓히지 않는다
    ctx2 = make_ctx()
    r2, _ = run(ctx2, "list_takes", {"mode": "PRACTICE", "completed_fully": False})
    ev2 = ctx2.registry.get(r2.data["count"]["evidence_id"])
    assert ev2.path == "takes[pitch_id=p-1, completed_fully=false, mode=PRACTICE].count"
    assert r2.data["count"]["filtered"] is True
    assert compose.none_kind(ev2) == "TAKES_FILTERED" and compose.none_sentence(ctx2, ev2) is None
    r3, _ = run(ctx2, "list_takes", {"mode": "아무 글 {x}"})
    assert ctx2.registry.get(r3.data["count"]["evidence_id"]).path.endswith("mode=OTHER].count")
    # Take 가 있으면 '없음' 근거를 만들지 않는다
    r4, _ = run(ctx2, "list_takes", {})
    assert "count" not in r4.data


def test_review_missing_is_review_status_evidence_not_take_none() -> None:
    req = load(base_request(), "t-4")
    req["context"]["pitch"]["focus_take"]["results"]["review"] = None
    ctx = make_ctx(req)
    for section in ("dimensions", "improvements", "mission_results"):
        res, _ = run(ctx, "get_take_report", {"take_id": "t-5", "section": section})
        ev = ctx.registry.get(res.data["review_status"]["evidence_id"])
        assert (ev.path, ev.value, ev.take_number, ev.kind) == (
            "review.status",
            "NONE",
            5,
            "REVIEW",
        )
        assert res.target_ids == ["t-5", "p-1"]
    assert compose.none_sentence(ctx, ev) == "Take 5에는 아직 리뷰 결과가 없어요."
    kinds = {compose.none_kind(e) for e in ctx.registry.items.values()}
    assert "TAKES" not in kinds  # 리뷰 없음은 'Take 없음'과 섞이지 않는다
    run5 = _read(ctx, "get_take_report", {"take_id": "t-5", "section": "dimensions"})
    assert (run5.result, run5.signal) == ("리뷰 분석 전", "REVIEW_MISSING")
    # 리뷰가 있으면 리뷰 상태 근거를 내지 않는다
    res4, _ = run(ctx, "get_take_report", {"take_id": "t-4", "section": "dimensions"})
    assert "review_status" not in res4.data


def test_problem_segments_zero_only_for_measured_areas() -> None:
    args = {"take_id": "t-5", "section": "problem_segments", "slide_number": 2}
    # 모든 영역이 측정됐고 2장에 구간이 없다: 영역마다 0곳 근거(장 표시)
    ctx = make_ctx(with_take_result(load(base_request(), "t-4"), issues=[]))
    res, _ = run(ctx, "get_take_report", args)
    rows = {r["area"]: r for r in res.data["no_segment_areas"]}
    assert set(rows) == {"GAZE", "SPEED", "VOLUME", "PAUSE", "FILLER", "TIME"}
    gaze = ctx.registry.get(rows["GAZE"]["count"]["evidence_id"])
    assert (gaze.path, gaze.value, gaze.slide_number, gaze.take_number) == (
        "problem_segments[area=GAZE].count",
        0,
        2,
        5,
    )
    assert "unmeasured_areas" not in res.data
    gaze_run = _read(ctx, "get_take_report", args, areas=frozenset({"GAZE"}))
    assert (gaze_run.result, gaze_run.signal, gaze_run.facts) == (
        "문제 구간 없음",
        "NO_RECORD",
        ["PROBLEM_SEGMENTS"],
    )
    sents = compose.none_partial(ctx, res.evidence_ids, areas=frozenset({"GAZE"}))
    assert [c.text for c in sents] == ["Take 5 2장의 시선에서 찾은 문제 구간이 없어요."]
    # 물은 영역을 모르면 모든 영역이 0곳일 때만 한 문장(영역 이름 없이)
    assert [c.text for c in compose.none_partial(ctx, res.evidence_ids)] == [
        "Take 5 2장에서 찾은 문제 구간이 없어요."
    ]
    # 시선이 측정 안 됨: 시선 '없음' 근거 · 문장이 없고 단계는 '측정 안 됨'(문제 구간 없음 아님)
    ctx2 = make_ctx(
        with_take_result(
            load(base_request(), "t-4"), issues=[], unmeasured={"GAZE": "LOW_MEASURED_RATIO"}
        )
    )
    res2, _ = run(ctx2, "get_take_report", args)
    assert "GAZE" not in {r["area"] for r in res2.data["no_segment_areas"]}
    assert res2.data["unmeasured_areas"] == [{"area": "GAZE", "label": "시선"}]
    for areas in (frozenset({"GAZE"}), None):
        r = _read(ctx2, "get_take_report", args, areas=areas)
        assert (r.result, r.signal) == ("측정 안 됨", None)
    assert compose.none_partial(ctx2, res2.evidence_ids, areas=frozenset({"GAZE"})) == []
    assert compose.none_partial(ctx2, res2.evidence_ids) == []
    # 구간이 있는 영역은 0곳 근거가 없다(다른 영역은 0곳)
    ctx3 = make_ctx(with_take_result(load(base_request(), "t-4"), issues=[("PACE_FAST", 2, True)]))
    res3, _ = run(ctx3, "get_take_report", args)
    assert "SPEED" not in {r["area"] for r in res3.data["no_segment_areas"]}
    assert (
        _read(ctx3, "get_take_report", args, areas=frozenset({"GAZE"})).result == "문제 구간 없음"
    )
    assert (
        _read(ctx3, "get_take_report", args, areas=frozenset({"SPEED"})).result == "문제 구간 있음"
    )


def test_get_goals_none_is_evidence_but_another_pitchs_goal_is_not() -> None:
    ctx = make_ctx()
    res, _ = run(ctx, "get_goals", {})
    st = res.data["conversation_goal"]["status"]
    ev = ctx.registry.get(st["evidence_id"])
    assert (ev.path, ev.value, ev.kind, ev.pitch_id) == ("goal.status", "NONE", "GOAL", "p-1")
    assert res.target_ids == ["p-1"]
    assert compose.none_sentence(ctx, ev) == "대화 목표는 아직 없어요."
    assert (_read(ctx, "get_goals", {}).result, _read(ctx, "get_goals", {}).signal) == (
        "목표 없음",
        "NO_RECORD",
    )
    # 다른 피치의 대화 목표만 있으면 '없음'이 아니다
    req = load(base_request(), "t-4")
    ex = json.loads((F.EXAMPLES / "request_take_reviewed.json").read_text(encoding="utf-8"))
    goal = copy.deepcopy(ex["context"]["conversation_goal"])
    goal["pitch_id"] = "p-2"
    req["context"]["conversation_goal"] = goal
    req["context"]["goal_version"] = goal["version"]
    ctx2 = make_ctx(req)
    res2, _ = run(ctx2, "get_goals", {})
    assert "status" not in res2.data["conversation_goal"]
    assert _read(ctx2, "get_goals", {}).signal is None


# ── 2. 답 검사: '없음'을 평가로 바꾸지 않는다 ─────────────────────────────────


def test_none_only_record_with_praise_word_is_caught() -> None:
    ctx = make_ctx(with_take_result(load(base_request(), "t-4"), issues=[]))
    res, _ = run(
        ctx, "get_take_report", {"take_id": "t-5", "section": "problem_segments", "slide_number": 2}
    )
    gaze = next(r for r in res.data["no_segment_areas"] if r["area"] == "GAZE")
    eid = gaze["count"]["evidence_id"]

    def check(text: str) -> list[str]:
        claim = Claim(kind="RECORD", text=text, evidence_ids=[eid])
        return [f.check_id for f in chk.check_claims([claim], registry=ctx.registry, cfg=CFG)]

    assert check("Take 5 2장의 시선에서 찾은 문제 구간이 없어요.") == []
    assert check("Take 5 2장 시선 문제 구간은 0곳이에요.") == []
    for praise in (
        "Take 5 2장 시선은 문제없어요.",
        "Take 5 2장 시선은 완벽해요.",
        "2장 시선은 잘하셨어요.",
    ):
        assert "AGREEMENT_WORD" in check(praise), praise
    # Take 수 0 근거로 '0개'를 말할 수 있다(숫자는 이은 근거와 대조)
    ctx2 = make_ctx(no_take_request())
    r2, _ = run(ctx2, "list_pitches", {})
    tc = r2.data["items"][0]["take_count"]["evidence_id"]
    ok = Claim(kind="RECORD", text="'졸업 발표'에는 Take가 0개예요.", evidence_ids=[tc])
    bad = Claim(kind="RECORD", text="'졸업 발표'에는 Take가 2개예요.", evidence_ids=[tc])
    assert chk.check_claims([ok], registry=ctx2.registry, cfg=CFG) == []
    assert [f.check_id for f in chk.check_claims([bad], registry=ctx2.registry, cfg=CFG)] == [
        "NUMBER_MISMATCH"
    ]


def test_answer_prompt_says_none_is_evidence_with_its_scope() -> None:
    assert "'없음 · 0'" in prompts.ANSWER and "문제 구간 0곳 ≠ 문제없음" in prompts.ANSWER


# ── 3. 사용자 시험 재현 (Take 없는 피치 · '가장 최근에 한 발표') ────────────────

NO_TAKE_PLAN = F.plan(steps=["최근 발표 찾기", "답 쓰기"], step_tools=["list_pitches", "answer"])


def _no_take_turn(answer: Any, *, fixes: list[Any] | None = None) -> tuple[Any, Any, Any]:
    llm = F.FakeLlm(
        [NO_TAKE_PLAN, F.tools(("list_pitches", {})), F.done()],
        answers=[answer],
        fixes=fixes or [F.fix({0: None})],
    )
    log: list[dict[str, Any]] = []
    _, lines, data = F.run_turn(no_take_request(), llm, log=log)
    return lines, data, log


def _assert_no_take_done(lines: Any, data: dict[str, Any], log: list[dict[str, Any]]) -> FinalData:
    fd = check_stream(lines, data)
    assert fd.status == "DONE"
    said = texts(data)
    assert T.PARTIAL_CHECKED.value not in said and T.VERIFIED_ONLY.value not in said
    # 다음 걸음: 연습 안내 문장이 끝에, [연습 준비로] 버튼(BUTTON, 자동 이동 없음)
    assert said[-1] == T.NEXT_FIRST_TAKE.value
    assert [(a.type, a.autonomy) for a in fd.actions] == [("START_PRACTICE", "BUTTON")]
    # 할 일 단계 결과는 'Take 없음', 끝난 계획에 남은 단계 없음
    assert data["plan"][0] == {
        "step": "최근 발표 찾기: 피치 목록 → Take 없음",
        "status": "DONE",
        "note": None,
    }
    assert [p["status"] for p in data["plan"]] == ["DONE", "DONE"]
    assert ("NO_RECORD", "FACT_SHOWN", "CODE", 1) in decisions(log)
    # 근거 모양 그대로: '없음' 근거도 evidence 사본에 RECORD 로 실린다
    ev = {e["evidence_id"]: e for e in data["evidence"]}
    rec = next(c for c in data["message"]["claims"] if c["kind"] == "RECORD")
    assert all(ev[e]["origin"] == "RECORD" for e in rec["evidence_ids"])
    return fd


def test_user_test_model_links_the_take_count_none_evidence_and_ends_done() -> None:
    def answer(r: Any) -> Any:
        return F.answer([F.claim("RECORD", NO_TAKE_SENTENCE, evidence_ids=[none_ev(r)])])

    lines, data, log = _no_take_turn(answer)
    _assert_no_take_done(lines, data, log)
    assert texts(data) == [NO_TAKE_SENTENCE, T.NEXT_FIRST_TAKE.value]


def test_user_test_all_model_sentences_dropped_code_draws_the_none_sentence() -> None:
    """모델 문장(근거 없음)이 검사로 모두 빠져도 일반 문구 대신 코드가 '없음' 근거 문장을 그리고 DONE(사용자 시험 그대로)."""
    lines, data, log = _no_take_turn(
        F.answer([F.claim("RECORD", "아직 발표 기록이 없어서 보여 드릴 수 없어요.")])
    )
    _assert_no_take_done(lines, data, log)
    assert texts(data) == [NO_TAKE_SENTENCE, T.NEXT_FIRST_TAKE.value]
    rec = data["message"]["claims"][0]
    assert rec["kind"] == "RECORD" and rec["evidence_ids"]


def test_no_take_next_step_is_sentence_only_when_another_action_exists() -> None:
    """버튼은 기존 START_PRACTICE 규칙을 넘지 않는다: 모델이 이미 버튼을 냈으면 하나만, 문장은 한 번."""

    def answer(r: Any) -> Any:
        return F.answer(
            [F.claim("RECORD", NO_TAKE_SENTENCE, evidence_ids=[none_ev(r)])],
            start_practice={"authorized_by": None},
        )

    _, data, _ = _no_take_turn(answer)
    fd = final_ok(data)
    assert [a.type for a in fd.actions] == ["START_PRACTICE"]
    assert texts(data).count(T.NEXT_FIRST_TAKE.value) == 1


def test_name_search_zero_does_not_widen_to_no_pitches() -> None:
    req = global_request("'창업 경진대회' 발표 기록 보여 줘")
    llm = F.FakeLlm(
        [F.plan(), F.tools(("list_pitches", {"query": "창업 경진대회"})), F.done()],
        answers=[F.answer([F.claim("RECORD", "피치가 하나도 없어요.")])],
        fixes=[F.fix({0: None})],
    )
    _, lines, data = F.run_turn(req, llm)
    fd = check_stream(lines, data)
    assert fd.status == "DONE"
    assert texts(data) == [T.PITCH_NOT_FOUND.value]  # 이름 검색 결과의 고정 문장이 답이다
    assert not any("피치가 없" in t or "발표가 없" in t for t in texts(data))


def test_review_missing_fallback_says_review_not_take() -> None:
    req = F.request(user_message="Take 5 리뷰 결과랑 점수 알려 줘")
    req["context"]["pitch"]["focus_take"]["results"]["review"] = None
    llm = F.FakeLlm(
        [
            F.plan(steps=["리뷰 보기", "답 쓰기"], step_tools=["get_take_report", "answer"]),
            F.tools(("get_take_report", {"take_id": "t-5", "section": "dimensions"})),
            F.done(),
        ],
        answers=[F.answer([F.claim("RECORD", "Take 5 점수는 70점이에요.")])],
        fixes=[F.fix({0: None})],
    )
    _, lines, data = F.run_turn(req, llm)
    fd = check_stream(lines, data)
    assert fd.status == "DONE"
    assert texts(data) == ["Take 5에는 아직 리뷰 결과가 없어요."]
    assert data["plan"][0]["step"] == "리뷰 보기: Take 5 기록 → 리뷰 분석 전"
    assert not any("Take 없음" in p["step"] for p in data["plan"])
    assert T.NEXT_FIRST_TAKE.value not in texts(data)  # Take 는 있다


# ── 4. 끊김 · 도구 실패와 끝 상태 ─────────────────────────────────────────────


def test_research_time_out_with_some_evidence_is_partial_with_todo() -> None:
    clock = F.Clock()
    plan = F.plan(
        steps=["최근 발표 찾기", "기록 보기", "답 쓰기"],
        step_tools=["list_pitches", "get_take_report", "answer"],
    )

    def slow(_r: Any) -> Any:
        clock.advance(50)  # 조사 시간(45초)을 넘긴다
        return F.tools(("list_pitches", {}))

    def answer(r: Any) -> Any:
        return F.answer([F.claim("RECORD", NO_TAKE_SENTENCE, evidence_ids=[none_ev(r)])])

    log: list[dict[str, Any]] = []
    _, lines, data = F.run_turn(
        no_take_request(), F.FakeLlm([plan, slow], answers=[answer]), clock=clock, log=log
    )
    fd = check_stream(lines, data)
    assert fd.status == "PARTIAL"
    assert [p["status"] for p in data["plan"]] == ["DONE", "TODO", "DONE"]
    assert data["plan"][1]["step"].endswith("시간 부족")
    said = texts(data)
    assert said[0] == T.LIMIT_PARTIAL.value and NO_TAKE_SENTENCE in said
    assert T.PARTIAL_CHECKED.value not in said
    assert ("BUDGET_LOW", "STOP_PARTIAL", "CODE") in [d[:3] for d in decisions(log)]


def test_research_failure_without_any_evidence_keeps_the_old_paths() -> None:
    """근거가 전혀 없는 장애: 답 쓰기도 실패하면 기존 오류 경로(error 줄)."""
    llm = F.FakeLlm([F.plan(), LlmTimeout()], answers=[LlmTimeout()])
    _, lines, data = F.run_turn(F.request(user_message="발표 기록 좀 봐 줘"), llm)
    assert lines[-1]["event"] == "error"


def test_recovered_tool_failure_is_done_but_unrecovered_is_partial() -> None:
    bad = (
        "get_take_report",
        {"take_id": "t-9", "section": "slides"},
    )  # 범위에 없는 Take: 확인 실패
    plan = F.plan(steps=["최근 기록 보기", "답 쓰기"], step_tools=["get_take_report", "answer"])
    dropped = F.answer([F.claim("RECORD", "기록을 찾았어요.")])

    def turn(*research: Any) -> dict[str, Any]:
        llm = F.FakeLlm([plan, *research, F.done()], answers=[dropped], fixes=[F.fix({0: None})])
        _, lines, data = F.run_turn(no_take_request(), llm)
        check_stream(lines, data)
        return data

    # 실패 뒤 다른 도구로 확인했다(복구): 그 이력만으로 PARTIAL 이 아니다
    done = turn(F.tools(bad, prefix="a"), F.tools(("list_pitches", {}), prefix="b"))
    assert done["status"] == "DONE"
    assert texts(done)[:1] == [NO_TAKE_SENTENCE]
    assert [p["status"] for p in done["plan"]] == ["DONE", "SKIPPED", "DONE"]
    assert done["plan"][1]["step"].endswith("확인 실패")
    # 확인한 뒤 실패하고 복구하지 않았다: 남은 부분이 있어 PARTIAL, 실패 단계는 TODO
    left = turn(F.tools(("list_pitches", {}), prefix="b"), F.tools(bad, prefix="a"))
    assert left["status"] == "PARTIAL"
    assert texts(left)[0] == T.VERIFIED_ONLY.value and NO_TAKE_SENTENCE in texts(left)
    assert "TODO" in [p["status"] for p in left["plan"]]


def test_unresolved_steps_and_end_status() -> None:
    def meta(**kw: Any) -> dict[str, Any]:
        base = {"done": False, "failed": False, "skipped": False, "result": None, "tool_seqs": []}
        return {**base, **kw}

    ok = meta(done=True, result="Take 없음", tool_seqs=[2])
    failed = meta(failed=True, result="확인 실패", tool_seqs=[1])
    assert progress.unresolved_steps([failed, ok]) == []  # 뒤에 성공한 실행이 있다
    assert progress.unresolved_steps([ok, meta(failed=True, tool_seqs=[3])]) == [1]
    assert progress.unresolved_steps([meta(result="시간 부족")]) == [0]
    assert progress.unresolved_steps([meta()]) == []  # 실행하지 않은 단계는 건너뜀
    assert progress.end_status([ok], cut=False, answered=True) == "DONE"
    assert progress.end_status([ok], cut=True, answered=True) == "PARTIAL"
    assert progress.end_status([ok], cut=False, answered=False) == "PARTIAL"


# ── 5. 결과 발견 안내 (목표 저장 성공 뒤) ─────────────────────────────────────


def _resume_save(result: list[dict[str, Any]], **dep_kw: Any) -> dict[str, Any]:
    req, _, data = pause()
    req2 = resume_request(req, data, result(data["actions"][0]))
    llm2 = F.FakeLlm([F.done()], answers=[F.answer([])], fixes=[F.fix({})])
    _, lines, d2 = F.run_turn(req2, llm2, **dep_kw)
    final_ok(d2)
    return d2


def test_saved_goal_adds_result_discovery_right_after_the_success_sentence_once() -> None:
    d2 = _resume_save(saved_result)
    claims = d2["message"]["claims"]
    i = next(k for k, c in enumerate(claims) if c["kind"] == "ACTION_RESULT")
    assert claims[i]["text"] == templates.ACTION_SUCCESS["SAVE_GOAL"]
    assert (claims[i + 1]["kind"], claims[i + 1]["text"]) == ("SYSTEM", T.RESULT_DISCOVERY.value)
    assert texts(d2).count(T.RESULT_DISCOVERY.value) == 1
    assert T.RESULT_DISCOVERY.value == "연습 뒤 대화를 다시 열면 목표 확인 결과를 볼 수 있어요."


def test_no_result_discovery_on_failure_button_only_or_switched_off() -> None:
    failed = _resume_save(
        lambda a: saved_result(
            a,
            status="FAILED",
            data=None,
            error={"code": "GOAL_VERSION_CONFLICT", "message": "다른 목표"},
        )
    )
    assert T.RESULT_DISCOVERY.value not in texts(failed)
    off = _resume_save(saved_result, cfg=load_config(result_discovery=False))
    assert T.RESULT_DISCOVERY.value not in texts(off)
    # 연습 준비 버튼만 낸 답(가벼운 말)
    _, _, d = F.run_turn(
        F.request(user_message="연습할래요"),
        F.FakeLlm([F.plan(route="SMALL_TALK", steps=[], talk_kind="PRACTICE_INTENT")]),
    )
    assert [a["type"] for a in d["actions"]] == ["START_PRACTICE"]
    assert T.RESULT_DISCOVERY.value not in texts(d)
    # 승인 대기 앞 답(아직 저장 전)에도 없다
    _, _, paused = pause()
    assert T.RESULT_DISCOVERY.value not in texts(paused)


# ── 6. 이어 부르기 사이에 원천이 지워지면 '없음' 근거도 무효 ───────────────────


def test_none_evidence_is_invalid_after_its_pitch_is_removed_between_calls() -> None:
    """다른 피치(투자 설명회, Take 0개)의 '없음' 근거를 읽고 가져오기로 멈춘 뒤, 이어 부를 때 그 피치가 지워졌으면 그 근거는
    버려진다: 그 근거에 이은 문장은 검사에 걸려 빠지고 근거 사본에도 없다(5-6, 기존 removed_sources 규칙)."""
    req = F.with_loaded(F.request(user_message="Take 3 기록이랑 다른 발표 Take 수 알려 줘"), "t-4")
    req["context"]["pitches"].append(
        {"pitch_id": "p-2", "title": "투자 설명회", "take_count": 0, "last_practiced_at": None}
    )
    req["context"]["pitches_total"] = 2
    seen: dict[str, str] = {}

    def fetch(r: Any) -> Any:
        seen["p2"] = none_ev(r)  # 투자 설명회 Take 수 0
        return F.tools(("fetch_take", {"take_id": "t-3", "reason": "Take 3"}), prefix="f")

    _, _, d1 = F.run_turn(req, F.FakeLlm([F.plan(), F.tools(("list_pitches", {})), fetch]))
    assert d1["status"] == "NEEDS_ACTION"
    action = d1["actions"][0]
    res = [
        {
            "action_id": action["action_id"],
            "type": "FETCH_TAKE",
            "status": "SUCCEEDED",
            "data": {"take": fetched_take("t-3")},
            "error": None,
        }
    ]
    req2 = resume_request(req, d1, res)
    req2["context"]["removed_sources"] = ["p-2"]
    gone = "'투자 설명회'에는 아직 연습한 Take가 없어요."
    llm2 = F.FakeLlm(
        [F.done()],
        answers=[F.answer([F.claim("RECORD", gone, evidence_ids=[seen["p2"]])])],
        fixes=[F.fix({0: None})],
    )
    _, _, d2 = F.run_turn(req2, llm2)
    fd = final_ok(d2)
    assert fd.status == "DONE"  # 가져오기 결과 문장이 답이다
    assert gone not in texts(d2)
    assert seen["p2"] not in {e["evidence_id"] for e in d2["evidence"]}
    fix = next(c for c in llm2.calls if c.purpose == "FIX")
    assert (
        "STALE_EVIDENCE" in fix.messages[-1]["content"]
        or "이번 턴에 읽은 근거가 아니" in fix.messages[-1]["content"]
    )


def test_fixed_templates_and_area_labels_do_not_collide_with_metric_names() -> None:
    """'없음' 문장의 영역 이름이 지표 이름으로 읽히면 대상 불일치로 걸린다: 영역마다 코드 문장이 검사를 통과한다."""
    ctx = make_ctx(with_take_result(load(base_request(), "t-4"), issues=[]))
    res, _ = run(
        ctx, "get_take_report", {"take_id": "t-5", "section": "problem_segments", "slide_number": 2}
    )
    for row in res.data["no_segment_areas"]:
        eid = row["count"]["evidence_id"]
        (c,) = compose.none_partial(ctx, [eid], areas=frozenset({row["area"]}))
        assert (
            chk.check_claims([c], registry=ctx.registry, code_sentences={c.text}, cfg=CFG) == []
        ), c.text
    assert templates.NONE_TAKES.format(title="졸업 발표") == NO_TAKE_SENTENCE
