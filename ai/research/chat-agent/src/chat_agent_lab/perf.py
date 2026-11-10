"""성능 표 — 평가 실행 폴더의 `runs.jsonl`에서 단계별 시간 · 호출 수 · 토큰 · 캐시 적중률 · 한도 종료 비율을 한국어 표로 낸다
(기능 문서 14-1 '비용 · 시간' · '첫 글자까지 시간', 11-5 진단 기록, 연구 전용).

실행(`ai/research/chat-agent`에서, 모델 호출 없음)::

    .venv/bin/python -m chat_agent_lab.perf outputs/evals/<시각> [outputs/evals/<다른 시각> ...] [--out perf.md]

- 입력: 실행 폴더의 `runs.jsonl`(evals.py가 쓴다). 말 턴(`kind == "message"`)만 센다.
- 턴 시간 · 첫 문장 시간은 evals.py 보고와 같은 정의다(일반 턴과 `REASSESS` 턴을 나눈다, 상위 5% = `evals.p95`).
- 단계별 시간은 차례의 `diag`(호출별 `[kind, code, start_ms, ms]`)에서 턴마다 합한 값의 중간값 · 상위 5%다.
  PLAN = 턴의 첫 `MODEL RESEARCH` · RESEARCH = 이어지는 `MODEL RESEARCH` + `TOOL` · ANSWER · CHECK · FIX = 같은 이름의 단계.
  `diag`가 없는 옛 기록은 단계 줄을 '기록 없음'으로 두고 나머지는 그대로 계산한다.
- 도구 캐시 적중률 = `TOOL` 단계 중 `outcome == "CACHED"`(`TOOL` 단계에만 다섯째 값으로 남긴다).
- 한도 종료 비율 = 끝 이유(`end_reason`)가 한도 이유(`evals.LIMIT_REASONS`)인 `PARTIAL` 턴 / 말 턴.
"""

from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path
from typing import Any

from chat_agent_lab.evals import LIMIT_REASONS, p95

NONE = "기록 없음"
PHASES = ("PLAN", "RESEARCH", "ANSWER", "CHECK", "FIX")


def load_turns(run_dir: Path) -> list[dict[str, Any]]:
    """`runs.jsonl`의 모든 차례(말 턴만)."""
    out: list[dict[str, Any]] = []
    path = run_dir / "runs.jsonl"
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        out.extend(t for t in json.loads(line).get("turns") or [] if t.get("kind") == "message")
    return out


def phase_ms(turn: dict[str, Any]) -> dict[str, int] | None:
    """한 턴의 단계별 걸린 시간(ms). `diag`가 없으면 None."""
    diag = turn.get("diag")
    if diag is None:
        return None
    out = dict.fromkeys(PHASES, 0)
    seen_plan = False
    for call in diag:
        for step in call.get("steps") or []:
            kind, code, ms = step[0], step[1], step[3] or 0
            if kind == "MODEL" and code == "RESEARCH":
                if seen_plan:
                    out["RESEARCH"] += ms
                else:
                    seen_plan = True
                    out["PLAN"] += ms
            elif kind == "TOOL":
                out["RESEARCH"] += ms
            elif kind == "MODEL" and code in ("ANSWER", "FIX"):
                out[code] += ms
            elif kind == "CHECK":
                out["CHECK"] += ms
    return out


def tool_outcomes(turns: list[dict[str, Any]]) -> tuple[int, int] | None:
    """(CACHED 수, 전체 `TOOL` 단계 수). 진단 기록이 있는 턴이 없으면 None."""
    if not any(t.get("diag") is not None for t in turns):
        return None
    cached = total = 0
    for t in turns:
        for call in t.get("diag") or []:
            for step in call.get("steps") or []:
                if step[0] == "TOOL":
                    total += 1
                    cached += len(step) > 4 and step[4] == "CACHED"
    return cached, total


def _med(values: list[float]) -> float | None:
    return statistics.median(values) if values else None


def _s(v: float | None, digits: int = 2) -> str:
    return NONE if v is None else f"{v:.{digits}f}"


def _row(name: str, values: list[float], unit: str = "초") -> str:
    if not values:
        return f"| {name} | 없음 | 없음 | 0 |"
    return f"| {name}({unit}) | {_s(_med(values))} | {_s(p95(values))} | {len(values)} |"


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def report(run_dir: Path) -> str:
    """실행 폴더 하나의 한국어 markdown 표."""
    turns = load_turns(run_dir)
    general = [t for t in turns if not t.get("reassess")]
    reas = [t for t in turns if t.get("reassess")]
    lines = [
        f"## {run_dir}",
        "",
        f"말 턴 {len(turns)}개(일반 {len(general)} · 다시 평가 {len(reas)})",
        "",
    ]
    lines += ["| 항목 | 중간값 | 상위 5% | 턴 수 |", "|---|---|---|---|"]
    lines.append(_row("턴 시간 일반", [t["wall_ms"] / 1000 for t in general]))
    lines.append(_row("턴 시간 REASSESS", [t["wall_ms"] / 1000 for t in reas]))
    lines.append(
        _row(
            "첫 문장까지 일반",
            [t["first_claim_ms"] / 1000 for t in general if t.get("first_claim_ms") is not None],
        )
    )
    phases = [p for p in map(phase_ms, turns) if p is not None]
    for ph in PHASES:
        if not phases:
            lines.append(f"| 단계 {ph}(초) | {NONE} | {NONE} | 0 |")
        else:
            lines.append(_row(f"단계 {ph}", [p[ph] / 1000 for p in phases]))
    lines += ["", "| 항목 | 값 |", "|---|---|"]
    n = len(turns)
    for label, key in (
        ("모델 호출 / 턴", "model_calls"),
        ("도구 호출 / 턴", "tool_calls"),
        ("입력 토큰 / 턴", "input_tokens"),
        ("캐시 입력 토큰 / 턴", "cached_input_tokens"),
        ("출력 토큰 / 턴", "output_tokens"),
    ):
        vals = [t[key] for t in turns if isinstance(t.get(key), int)]
        lines.append(f"| {label} | {_s(_mean(vals), 1)} |")
    oc = tool_outcomes(turns)
    if oc is None:
        lines.append(f"| 도구 캐시 적중률 | {NONE} |")
    elif oc[1] == 0:
        lines.append("| 도구 캐시 적중률 | 도구 단계 없음 |")
    else:
        lines.append(f"| 도구 캐시 적중률 | {oc[0] / oc[1]:.1%} ({oc[0]}/{oc[1]}) |")
    limited = sum(
        1
        for t in turns
        if t.get("end_status") == "PARTIAL" and t.get("end_reason") in LIMIT_REASONS
    )
    share = f"{limited / n:.1%} ({limited}/{n})" if n else "없음"
    lines.append(f"| 한도로 끝난 턴 비율 | {share} |")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="평가 실행 폴더의 단계별 시간 · 비용 표")
    ap.add_argument("dirs", nargs="+", type=Path, help="runs.jsonl이 있는 실행 폴더")
    ap.add_argument("--out", type=Path, default=None, help="표를 파일에도 쓴다")
    a = ap.parse_args(argv)
    text = "\n".join(report(d) for d in a.dirs)
    print(text)
    if a.out:
        a.out.write_text(text, encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
