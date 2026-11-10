"""평가 실행의 대화 기록 — 사람이 읽듯 대화를 보는 검토(`evals/reader-review/`)의 재료 (Issue #1194).

- 하는 일: 평가 실행기(`evals.main`)를 그대로 돌리면서, 실행마다 채점 직전의 대화 상태에서 **사용자 화면에 보이는 것**
  (사용자 말 · 할 일 단계와 상태 · 확인한 것(도구 기록 요약) · 근거 표시(읽은 Take) · 답 문장 · 카드)을 차례마다 뽑아
  결과 폴더에 `transcripts.jsonl`로 쓴다. 채점 · 실행 코드(`GRADER_FILES`)는 고치지 않는다: `evals.grade`를 이 모듈에서 감싸
  기록만 더한다(결과 · 점수는 같은 명령의 `evals`와 같다). 독립 평가 v2 의 공식 실행에는 쓰지 않는다(동결된 실행기 그대로).
- 실행(연구 폴더에서, 요금이 듦): `.venv/bin/python -m chat_agent_lab.transcripts <evals 인자 그대로>`
  예: `... -m chat_agent_lab.transcripts --dir evals/path --set all --workers 4 --search hybrid --out outputs/evals/x`
- 출력 한 줄: `{scenario, rep, passed, turns: [{message, status, plan: [{status, step}], checked: [글], basis: [Take 번호],
  claims: [{kind, text}], cards: [종류]}]}`. 근거 id · 내부 칸은 넣지 않는다(화면에 보이지 않는다).
- 고칠 때: 화면에 보이는 칸이 바뀌면(FE 12-4) `screen_turn`을 같이 고친다. 평가 실행기의 `grade` · `TurnRecord` 이름이 바뀌면
  이 감싸기도 고친다.
"""

from __future__ import annotations

import json
import sys
import threading
from pathlib import Path
from typing import Any

from . import evals as ev

_LOCK = threading.Lock()
_ROWS: list[dict[str, Any]] = []


def screen_turn(runs: list[Any]) -> dict[str, Any]:
    """한 차례(사용자 말 하나)의 실행들에서 화면에 보이는 것: 마지막 끝 줄의 할 일 단계 · 답 · 카드, 실행 전체의 확인한 것."""
    message = ""
    finals = []
    checked: list[str] = []
    for run in runs:
        message = message or str((run.original or {}).get("user_message") or "")
        for call in run.calls:
            fin = call.final
            if not fin:
                continue
            finals.append(fin)
            for t in fin.get("trace") or []:
                text = " → ".join(x for x in (t.get("args_summary"), t.get("result_summary")) if x)
                if text and text not in checked:
                    checked.append(text)
    last = finals[-1] if finals else {}
    msg = last.get("message") or {}
    takes = sorted(
        {
            int(t["take_number"])
            for c in msg.get("claims") or []
            for t in ((c.get("basis") or {}).get("takes") or [])
            if isinstance(t, dict) and isinstance(t.get("take_number"), int)
        }
    )
    return {
        "message": message,
        "status": last.get("status"),
        "plan": [{"status": p.get("status"), "step": p.get("step")} for p in last.get("plan") or []],
        "checked": checked,
        "basis_takes": takes,
        "claims": [{"kind": c.get("kind"), "text": c.get("text")} for c in msg.get("claims") or []],
        "cards": [c.get("type") for c in msg.get("cards") or []],
    }


def _wrap(orig: Any) -> Any:
    def grade(ctx: Any) -> Any:
        out = orig(ctx)
        try:
            row = {
                "scenario": ctx.sc.id,
                "passed": bool(out[0]),
                "turns": [screen_turn(t.runs) for t in ctx.turns],
            }
        except Exception as e:  # noqa: BLE001 — 기록을 못 뽑아도 평가는 그대로 간다
            row = {"scenario": getattr(getattr(ctx, "sc", None), "id", "?"), "error": type(e).__name__}
        with _LOCK:
            _ROWS.append(row)
        return out

    return grade


def main(argv: list[str] | None = None) -> None:
    args = list(sys.argv[1:] if argv is None else argv)
    out = Path(args[args.index("--out") + 1]) if "--out" in args else None
    ev.grade = _wrap(ev.grade)  # type: ignore[assignment]
    try:
        ev.main(args)
    finally:
        if out is not None:
            out.mkdir(parents=True, exist_ok=True)
            seen: dict[str, int] = {}
            with (out / "transcripts.jsonl").open("w", encoding="utf-8") as f:
                for row in _ROWS:
                    rep = seen.get(row["scenario"], 0)
                    seen[row["scenario"]] = rep + 1
                    f.write(json.dumps({**row, "rep": rep}, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    main()
