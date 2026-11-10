"""성능 표(perf.py) 시험 — 모델 없이 만든 runs.jsonl 줄로 단계 합산 · 옛 형식 · 한도 비율을 본다."""

from __future__ import annotations

import json
from pathlib import Path

from chat_agent_lab import perf


def _turn(wall: int, diag: list | None, **kw) -> dict:
    t = {
        "kind": "message",
        "reassess": False,
        "wall_ms": wall,
        "first_claim_ms": 500,
        "model_calls": 3,
        "tool_calls": 1,
        "input_tokens": 100,
        "cached_input_tokens": 40,
        "output_tokens": 10,
        "end_status": "DONE",
        "end_reason": "ANSWERED",
    }
    t.update(kw)
    if diag is not None:
        t["diag"] = diag
    return t


def _write(d: Path, turns: list[dict]) -> Path:
    d.mkdir(parents=True, exist_ok=True)
    (d / "runs.jsonl").write_text(json.dumps({"turns": turns}) + "\n", encoding="utf-8")
    return d


DIAG = [
    {
        "steps": [
            ["MODEL", "RESEARCH", 0, 1000],
            ["TOOL", "get_pitch", 1000, 100, "OK"],
            ["TOOL", "get_pitch", 1100, 1, "CACHED"],
            ["MODEL", "RESEARCH", 1200, 800],
            ["MODEL", "ANSWER", 2000, 3000],
            ["CHECK", "SUMMARY", 5000, 50],
            ["MODEL", "FIX", 5050, 400],
        ],
        "end": "ANSWERED",
    }
]


def test_phase_ms_split():
    p = perf.phase_ms(_turn(6000, DIAG))
    assert p == {"PLAN": 1000, "RESEARCH": 901, "ANSWER": 3000, "CHECK": 50, "FIX": 400}
    assert perf.phase_ms(_turn(6000, None)) is None


def test_report_new_format(tmp_path):
    turns = [
        _turn(6000, DIAG),
        _turn(8000, DIAG, reassess=True),
        _turn(9000, DIAG, end_status="PARTIAL", end_reason="TIME"),
    ]
    out = perf.report(_write(tmp_path / "a", turns))
    assert "턴 시간 일반(초) | 7.50" in out
    assert "턴 시간 REASSESS(초) | 8.00" in out
    assert "단계 PLAN(초) | 1.00" in out
    assert "도구 캐시 적중률 | 50.0% (3/6)" in out
    assert "한도로 끝난 턴 비율 | 33.3% (1/3)" in out
    assert "모델 호출 / 턴 | 3.0" in out


def test_report_old_format_has_no_phase(tmp_path):
    out = perf.report(_write(tmp_path / "old", [_turn(6000, None), _turn(10000, None)]))
    assert "단계 PLAN(초) | 기록 없음" in out
    assert "도구 캐시 적중률 | 기록 없음" in out
    assert "턴 시간 일반(초) | 8.00" in out
    assert "첫 문장까지 일반(초) | 0.50" in out


def test_main_writes_out(tmp_path):
    d = _write(tmp_path / "a", [_turn(6000, DIAG)])
    o = tmp_path / "x.md"
    assert perf.main([str(d), "--out", str(o)]) == 0
    assert o.read_text(encoding="utf-8").startswith("## ")
