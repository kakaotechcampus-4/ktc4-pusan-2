"""실행 기록 발췌 시험 — 가짜 모델로 파일이 써지고, 서명 상태가 가려지고, 결정 로그가 들어가는지 (실제 모델 없음)."""

from __future__ import annotations

import json
from pathlib import Path

from chat_agent_lab import evals as ev
from chat_agent_lab import runlog as rl
from chat_agent_lab import scenario as scn
from chat_agent_lab.service_tests import fakes as F


def _run(tmp_path: Path, sid: str, inner: ev.ToolLlm) -> tuple[str, list[dict]]:
    sc = scn.load_scenarios(ids=[sid])
    paths = rl.run_logs(sc, inner, F.SECRET, ev.RunnerConfig(repeat=1), tmp_path, model="fake")
    assert paths == [tmp_path / f"{sid}.md"]
    lines = [
        json.loads(x) for x in (tmp_path / f"{sid}.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    return paths[0].read_text(encoding="utf-8"), lines


def test_writes_md_and_jsonl_with_meta(tmp_path: Path) -> None:
    md, lines = _run(tmp_path, "refuse-out-of-scope", ev.DryRunLlm())
    assert md.startswith("# 실행 기록 발췌 — refuse-out-of-scope")
    for key in ("기능 버전", "프롬프트 버전", "모델: fake", "실행일"):
        assert key in md
    assert "| seq | event | 내용 |" in md and "AI 호출 1" in md
    kinds = {x["type"] for x in lines}
    assert {"meta", "user_message", "stream", "final"} <= kinds


def test_state_is_redacted() -> None:
    out = rl.redact(
        {"final": {"state": "서명값", "actions": [{"state": "x", "type": "A"}]}, "state": None}
    )
    s = json.dumps(out, ensure_ascii=False)
    assert "서명값" not in s and '"x"' not in s
    assert s.count(rl.REDACTED) == 2 and out["state"] is None


def test_decision_lines_are_included_when_present(tmp_path: Path) -> None:
    sc = scn.load_scenarios(ids=["refuse-out-of-scope"])[0]
    ctx = ev._Ctx(sc, ev.DryRunLlm(), F.SECRET, ev.RunnerConfig(repeat=1))
    for t in sc.turns:
        ctx.do_turn(t)
    call = ctx.turns[0].runs[0].calls[0]
    ctx.decisions[call.request_id] = [
        {
            "event": "agent_decision",
            "request_id": call.request_id,
            "signal": "SIG_X",
            "facts": {"n": 1},
            "decider": "CODE",
            "effect": "ASK",
            "step": 2,
            "tool_seq": 1,
        }
    ]
    md, lines = rl.render(
        ctx,
        {
            "feature_version": "v",
            "prompt_version": "p",
            "model": "m",
            "search": "keyword",
            "date": "2026-01-01",
        },
        passed=None,
    )
    assert "SIG_X" in md and "결정 로그" in md
    assert any(x["type"] == "decision" and x["signal"] == "SIG_X" for x in lines)
