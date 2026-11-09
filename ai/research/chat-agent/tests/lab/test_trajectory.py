"""경로(trajectory) 관찰값 · 비교 · 시나리오 형식 · 유사도 시험 — 가짜 Run · Call 을 손으로 만든다 (경로 채점 명세)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from chat_agent_lab import evals as ev
from chat_agent_lab import grading as gr
from chat_agent_lab import scenario as scn
from chat_agent_lab import similarity as sim
from chat_agent_lab.fake_be import ActionRecord, Call, Run


def _ev(seq: int, call: int, event: str, data: dict[str, Any]) -> dict[str, Any]:
    return {"event_seq": seq, "call": call, "event": event, "data": data}


def _tool(seq: int, call: int, tool: str, outcome: str = "OK", tseq: int | None = None):
    return _ev(
        seq, call, "tool_done", {"trace": {"seq": tseq or seq, "tool": tool, "outcome": outcome}}
    )


def _plan(seq: int, call: int, *steps: str):
    return _ev(seq, call, "plan", {"plan": [{"step": s, "status": "TODO"} for s in steps]})


def _run(calls: list[Call], events: list[dict[str, Any]], actions=()) -> Run:
    r = Run("run-1", "c-1", "u-1", "USER", original={}, history={})
    r.calls = calls
    r.events = events
    r.actions = list(actions)
    return r


def _call(n: int, final: dict[str, Any] | None, rid: str | None = None, code=None) -> Call:
    return Call(n, rid or f"req-{n}", {}, final=final, error_code=code)


def _simple_run() -> Run:
    events = [
        _plan(1, 1, "a", "b"),
        _tool(2, 1, "get_take_report"),
        _plan(3, 1, "a", "b"),
        _tool(4, 1, "update_plan"),
        _plan(5, 1, "a", "c"),  # 코드 표시 갱신
        _tool(6, 1, "compare_takes", "EMPTY"),
        _ev(7, 1, "claim", {"index": 0, "claim": {"kind": "RECORD", "text": "비밀"}}),
    ]
    final = {
        "status": "DONE",
        "plan": [{"step": "a", "status": "DONE"}, {"step": "c", "status": "TODO"}],
        "actions": [{"type": "START_PRACTICE", "autonomy": "BUTTON"}],
    }
    return _run([_call(1, final)], events)


def test_event_vocabulary_and_counts() -> None:
    t = ev.trajectory_obs([_simple_run()], {})
    assert t["events"] == [
        "plan:set",
        "tool:get_take_report:OK",
        "plan:edit",
        "tool:update_plan:OK",
        "plan:update",
        "tool:compare_takes:EMPTY",
        "claim:RECORD",
        "propose:START_PRACTICE:BUTTON",
        "final:DONE",
    ]
    assert (t["plan_edits"], t["plan_updates"]) == (1, 1)
    assert len(t["plans"]) == 3
    assert t["plan_texts"] == ["a", "c"]
    assert t["plan_statuses"] == ["DONE", "TODO"]
    assert "비밀" not in json.dumps(t["events"], ensure_ascii=False)


def test_decision_placement() -> None:
    decs = {
        "req-1": [
            {"signal": "NOT_COMPARABLE", "effect": "FACT_SHOWN", "decider": "CODE", "tool_seq": 2},
            {"signal": "TOOL_ERROR", "effect": "NONE", "decider": "LLM", "tool_seq": None},
            {"signal": "BUDGET_LOW", "effect": "NONE", "decider": "CODE", "tool_seq": None},
        ]
    }
    events = [
        _plan(1, 1, "a"),
        _tool(2, 1, "x"),
        _tool(3, 1, "y"),
        _ev(4, 1, "claim", {"index": 0, "claim": {"kind": "GUIDE"}}),
    ]
    run = _run([_call(1, {"status": "DONE", "actions": []})], events)
    out = ev.trajectory_obs([run], decs)["events"]
    assert out == [
        "plan:set",
        "tool:x:OK",
        "decision:NOT_COMPARABLE:FACT_SHOWN:CODE",
        "tool:y:OK",
        "decision:TOOL_ERROR:NONE:LLM",
        "decision:BUDGET_LOW:NONE:CODE",
        "claim:GUIDE",
        "final:DONE",
    ]


def test_approval_then_exec_between_calls() -> None:
    events = [_plan(1, 1, "a"), _plan(2, 2, "a")]
    act = {"type": "SAVE_GOAL", "autonomy": "ASK"}
    calls = [
        _call(1, {"status": "NEEDS_ACTION", "actions": [act]}),
        _call(2, {"status": "DONE", "actions": []}),
    ]
    rec = ActionRecord(act, {"status": "SUCCEEDED"}, executed=True, decision="APPROVED")
    rej = ActionRecord(act, {"status": "REJECTED"}, executed=False, decision="REJECTED")
    out = ev.trajectory_obs([_run(calls, events, [rec, rej])], {})["events"]
    assert out == [
        "plan:set",
        "propose:SAVE_GOAL:ASK",
        "final:NEEDS_ACTION",
        "approve:SAVE_GOAL",
        "exec:SAVE_GOAL:OK",
        "reject:SAVE_GOAL",
        "plan:update",
        "final:DONE",
    ]


def test_error_final_and_fail_exec() -> None:
    rec = ActionRecord({"type": "REASSESS"}, {"status": "FAILED"}, executed=True)
    run = _run([_call(1, None, code="STREAM_INVALID")], [], [rec])
    assert ev.trajectory_obs([run], {})["events"] == [
        "final:error:STREAM_INVALID",
        "exec:REASSESS:FAIL",
    ]


def test_turn_all_merge() -> None:
    a = ev.trajectory_obs([_simple_run()], {})
    merged = ev._merge([a, a], "x")
    assert merged["events"] == a["events"] * 2
    assert merged["plan_edits"] == 2
    assert merged["plan_updates"] == 2


# ── 시나리오 형식 ────────────────────────────────────────────────────────


def _check(**kw: Any) -> scn.Check:
    base = {"check_id": "p1", "observe": "trajectory", "path": "events", "compare": "in_order"}
    return scn.Check(**{**base, "expect": ["a"], **kw})


def test_path_compare_only_on_trajectory() -> None:
    _check()
    _check(compare="precedes", expect=["a", "b"], metric="path")
    _check(compare="in_order_any", expect=[["a"], ["b", "c"]], metric="replan")
    with pytest.raises(ValidationError):
        _check(observe="trace")
    with pytest.raises(ValidationError):
        _check(compare="precedes", expect=["a"])
    with pytest.raises(ValidationError):
        _check(compare="in_order_any", expect=["a"])
    with pytest.raises(ValidationError):
        _check(compare="in_order_any", expect=[])
    with pytest.raises(ValidationError):
        _check(grader="regex")


def test_schema_has_new_enums() -> None:
    text = json.dumps(scn.schema_json())
    for w in ("trajectory", "in_order_any", "not_in_order", "replan"):
        assert w in text


# ── 비교 ─────────────────────────────────────────────────────────────────

EVS = ["plan:set", "tool:get_take_report:OK", "tool:compare_takes:EMPTY", "final:DONE"]


def _run_check(compare: str, expect: Any, obs: Any = EVS):
    c = _check(compare=compare, expect=expect)
    return gr.run_check(c, {"events": obs})


def test_in_order() -> None:
    assert _run_check("in_order", ["plan:set", "tool:compare_takes:*", "final:*"]).passed
    r = _run_check("in_order", ["tool:compare_takes:*", "tool:get_take_report:*"])
    assert not r.passed and "tool:get_take_report:*" in r.reason
    assert not _run_check("in_order", ["a"], obs="문자열").passed  # 목록이 아님


def test_in_order_any() -> None:
    assert _run_check("in_order_any", [["final:*", "plan:set"], ["plan:set", "final:*"]]).passed
    assert not _run_check("in_order_any", [["x"], ["y"]]).passed


def test_precedes() -> None:
    assert _run_check("precedes", ["tool:get_take_report:*", "tool:compare_takes:*"]).passed
    assert not _run_check("precedes", ["tool:compare_takes:*", "tool:get_take_report:*"]).passed
    r = _run_check("precedes", ["plan:set", "tool:nope:*"])
    assert not r.passed and "B 없음" in r.reason  # 빈 통과 금지


def test_not_in_order() -> None:
    assert _run_check("not_in_order", ["tool:compare_takes:*", "plan:set"]).passed
    assert not _run_check("not_in_order", ["plan:set", "final:*"]).passed


def test_other_trajectory_fields_use_existing_compares() -> None:
    c = scn.Check(check_id="t", observe="trajectory", path="plan_edits", compare="equals", expect=1)
    assert gr.run_check(c, {"plan_edits": 1}).passed


# ── 유사도 ───────────────────────────────────────────────────────────────


def _write(folder: Path, sid: str, msg: str) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    d = {"id": sid, "turns": [{"message": msg}], "fixtures": {}}
    (folder / f"{sid}.json").write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")


def test_similarity_flag_pass_and_no_text(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    old, new = tmp_path / "old", tmp_path / "new"
    secret = "Take 3 의 청중 시선 비율이 왜 낮았는지 알려 주세요"
    _write(old, "o1", secret)
    (old / "schema.json").write_text("{}", encoding="utf-8")
    _write(new, "n-same", secret)
    _write(new, "n-diff", "발표 마지막에 질문이 나오면 어떻게 답하면 좋을까요 팁 부탁")
    out = tmp_path / "r.txt"
    code = sim.main(["--new", str(new), "--against", str(old), "--out", str(out)])
    shown = capsys.readouterr().out + out.read_text(encoding="utf-8")
    assert code == 1
    assert "n-same\to1\t1.000\tFLAG" in shown
    assert "n-diff" in shown and "PASS" in shown
    assert "PASS 1 · FLAG 1" in shown
    assert "청중" not in shown and "질문이" not in shown


def test_similarity_fails_on_unreadable_file_or_empty_side(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """읽지 못한 파일이 있거나 한쪽에 시나리오가 없으면 비교하지 않고 2(빈 비교를 PASS 로 끝내지 않는다). 파일 이름만 낸다."""
    old, new = tmp_path / "old", tmp_path / "new"
    secret = "Take 3 의 청중 시선 비율이 왜 낮았는지 알려 주세요"
    _write(old, "o1", secret)
    _write(new, "n1", "발표 마지막에 질문이 나오면 어떻게 답하면 좋을까요")
    assert sim.main(["--new", str(new), "--against", str(old)]) == 0
    capsys.readouterr()
    # 기존 쪽 파일 하나가 JSON 이 아님
    (old / "broken.json").write_text("{" + secret, encoding="utf-8")
    assert sim.main(["--new", str(new), "--against", str(old)]) == 2
    out = capsys.readouterr().out
    assert "읽지 못한 파일: broken.json" in out and "청중" not in out
    (old / "broken.json").unlink()
    # 객체가 아닌 JSON 도 읽지 못한 파일이다
    (new / "list.json").write_text("[1, 2]", encoding="utf-8")
    assert sim.main(["--new", str(new), "--against", str(old)]) == 2
    (new / "list.json").unlink()
    # 한쪽에 시나리오(차례가 있는 객체)가 없다
    empty = tmp_path / "empty"
    empty.mkdir()
    (empty / "notes.json").write_text('{"id": "x"}', encoding="utf-8")
    assert sim.main(["--new", str(empty), "--against", str(old)]) == 2
    assert "새 쪽에 시나리오가 없다" in capsys.readouterr().out
    assert sim.main(["--new", str(new), "--against", str(empty)]) == 2
    assert "기존 쪽에 시나리오가 없다" in capsys.readouterr().out


_SECRET = "Take 3 의 청중 시선 비율이 왜 낮았는지 알려 주세요"


def _raw(folder: Path, name: str, d: dict[str, Any]) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{name}.json").write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")


def test_similarity_invalid_id_is_masked_and_fails(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """id 가 `^[a-z0-9][a-z0-9-]*$` 꼴이 아니면(글 · 대문자 · 없음) 비교하지 않고 2. id 값은 내지 않고 `<invalid-id-N>`으로 가린다."""
    old, new = tmp_path / "old", tmp_path / "new"
    _write(old, "o1", "발표 마지막에 질문이 나오면 어떻게 답하면 좋을까요")
    _raw(new, "a", {"id": _SECRET, "turns": [{"message": "다른 질문 하나"}]})
    _raw(new, "b", {"id": "Upper-Case", "turns": [{"message": "또 다른 질문"}]})
    _raw(new, "c", {"turns": [{"message": "id 없는 문항"}]})
    _raw(new, "d", {"id": "ok-1\n", "turns": [{"message": "줄바꿈 붙은 id"}]})
    assert sim.main(["--new", str(new), "--against", str(old)]) == 2
    out = capsys.readouterr().out
    for n in (1, 2, 3, 4):
        assert f"id 형식이 아님: <invalid-id-{n}>" in out
    assert "청중" not in out and "Upper-Case" not in out and "비교하지 않음" in out


def test_similarity_duplicate_ids_within_and_among_folders_fail(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    old, old2, new = tmp_path / "old", tmp_path / "old2", tmp_path / "new"
    _write(old, "o1", "발표 마지막에 질문이 나오면 어떻게 답하면 좋을까요")
    _write(new, "n1", _SECRET)
    # 한 폴더 안: 파일 이름이 달라도 id 가 같다
    _raw(new, "n1-copy", {"id": "n1", "turns": [{"message": "다른 글"}]})
    assert sim.main(["--new", str(new), "--against", str(old)]) == 2
    out = capsys.readouterr().out
    assert out.count("id 가 겹친다: n1") == 1 and "청중" not in out
    (new / "n1-copy.json").unlink()
    # 새 쪽과 기존 쪽 사이
    _write(old2, "n1", "완전히 다른 기존 문항의 글")
    assert sim.main(["--new", str(new), "--against", str(old), "--against", str(old2)]) == 2
    assert "id 가 겹친다: n1" in capsys.readouterr().out
    # 기존 폴더 둘 사이
    (old2 / "n1.json").unlink()
    _raw(old2, "o1", {"id": "o1", "turns": [{"message": "기존 폴더끼리 겹친 id"}]})
    assert sim.main(["--new", str(new), "--against", str(old), "--against", str(old2)]) == 2
    assert "id 가 겹친다: o1" in capsys.readouterr().out


@pytest.mark.parametrize(
    "turns",
    [
        [{"text": _SECRET}],  # message 칸이 아님 → 비교할 글이 비어 PASS 가 되던 모양
        [{"message": ["목록"]}],
        [{"message": "   "}],
        [{}],
        ["글 차례"],
        [],
        {"message": _SECRET},
        [{"message": "글", "auto_goal_check": True}],  # 둘 다
    ],
    ids=["wrong-key", "list", "blank", "empty-turn", "str-turn", "no-turns", "not-list", "both"],
)
def test_similarity_malformed_turns_fail_without_printing_text(
    turns: Any, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    old, new = tmp_path / "old", tmp_path / "new"
    _write(old, "o1", "발표 마지막에 질문이 나오면 어떻게 답하면 좋을까요")
    _write(new, "n-ok", "다른 질문 하나")
    _raw(new, "n-bad", {"id": "n-bad", "turns": turns})
    assert sim.main(["--new", str(new), "--against", str(old)]) == 2
    out = capsys.readouterr().out
    assert "차례 형식이 틀림(turns[].message · auto_goal_check): n-bad" in out
    assert "청중" not in out and "n-ok" not in out
    # 기존 쪽에 있어도 같다
    assert sim.main(["--new", str(old), "--against", str(new)]) == 2


def test_similarity_auto_goal_check_only_scenario_is_valid_but_counted(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """자동 목표 확인 차례만 있는 문항은 형식대로다(오류 아님). 비교할 글이 없어 점수 0 이므로 끝에 그 수를 따로 적는다."""
    old, new = tmp_path / "old", tmp_path / "new"
    _write(old, "o1", "발표 마지막에 질문이 나오면 어떻게 답하면 좋을까요")
    _raw(new, "n-auto", {"id": "n-auto", "turns": [{"auto_goal_check": True}]})
    _write(new, "n1", "다른 질문 하나")
    assert sim.main(["--new", str(new), "--against", str(old)]) == 0
    out = capsys.readouterr().out
    assert "PASS 2 · FLAG 0" in out
    assert "글이 없어 점수 0 인 문항(자동 목표 확인 차례만): 1" in out


def test_trajectory_default_paths_and_regex() -> None:
    """경로 관찰값의 기본 경로: 값 비교는 events, regex 는 plan_text(단계 글을 이은 것)."""
    from chat_agent_lab import grading as gr
    from chat_agent_lab import scenario as scn

    obs = {
        "events": ["plan:set", "tool:compare_takes:OK", "final:DONE"],
        "plan_text": "반복 확인: Take 3·4 비교 → 비교 불가(대본 버전 다름)",
    }
    order = scn.Check(
        check_id="o", observe="trajectory", compare="in_order", expect=["plan:set", "final:*"]
    )
    assert gr.run_check(order, obs).passed
    rx = scn.Check(
        check_id="r",
        observe="trajectory",
        grader="regex",
        compare="contains",
        expect="비교 불가.*대본",
    )
    assert gr.run_check(rx, obs).passed
