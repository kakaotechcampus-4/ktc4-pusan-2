"""회귀 재측정 결과에서 실행 로그 발췌를 뽑는다 — 모델을 다시 부르지 않는다(연구 전용, Issue #1194).

- 입력: `chat_agent_lab.transcripts`로 돈 결과 폴더(`runs.jsonl` · `transcripts.jsonl`). 문항마다 첫 실행을 쓴다.
- 출력: 문항마다 `<id>.md` — 차례마다 화면 할 일 단계 · 확인한 것 · 답(문장 종류와 글) · 카드, 마지막 차례의 결정 로그
  (`agent_decision`: 신호 · 효과 · 결정자)와 사건 순서. 대화 기록에는 근거 id · 서명 상태가 없다(화면에 보이는 것만).
- `runlog.py`와의 차이: `runlog.py`는 시나리오를 새로 한 번 돌려 호출마다 스트림 줄을 남기고(요금이 듦), 이 도구는 이미 잰
  재측정의 실행을 그대로 옮긴다(채점 결과도 그 측정의 것).

실행(`ai/research/chat-agent`에서)::

    .venv/bin/python -m chat_agent_lab.runlog_from_run outputs/evals/<결과 폴더> reports/run-logs/ path-rm-score-ask path-fail-budget-partial
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ICON = {"DONE": "☑", "DOING": "⟳", "TODO": "☐", "SKIPPED": "—"}


def render(sid: str, run: dict, tr: dict, source: str) -> str:
    """한 문항의 발췌(md)."""
    fails = [c["check_id"] for c in run["checks"] if not c.get("passed")]
    meta = run.get("meta") if isinstance(run.get("meta"), dict) else {}
    md = [
        f"# 실행 기록 발췌 — {sid}",
        "",
        f"- 기능 버전: {meta.get('feature_version', '?')} · 프롬프트 버전: {meta.get('prompt_version', '?')} · 모델: {meta.get('model', '?')}",
        f"- 출처: 회귀 재측정 `{source}`의 첫 실행(가짜 BE · 합성 자료, 모델을 다시 부르지 않고 옮김)",
        f"- 채점 결과(참고): {'통과' if run['passed'] else '실패(' + ', '.join(fails) + ')'}",
        "",
    ]
    for i, turn in enumerate(tr["turns"], 1):
        md += [f"## 차례 {i} — {turn['message']}", "", f"끝 상태 `{turn['status']}`", "", "할 일 단계(화면):", ""]
        md += [f"- {ICON.get(p['status'], p['status'])} {p['step']}" for p in turn["plan"]] or ["- (없음)"]
        md += ["", "확인한 것(화면):", ""] + ([f"- {c}" for c in turn["checked"]] or ["- (없음)"])
        md += ["", "답(문장 종류 · 글):", ""] + ([f"- `{c['kind']}` {c['text']}" for c in turn["claims"]] or ["- (없음)"])
        if turn.get("cards"):
            md += ["", "카드: " + ", ".join(turn["cards"])]
        md.append("")
    dec = [e for e in run["trajectory"] if e.startswith("decision:")]
    md += ["## 결정 로그(`agent_decision` 신호:효과:결정자, 마지막 차례)", ""] + ([f"- `{e[len('decision:'):]}`" for e in dec] or ["- (없음)"])
    events = [e for e in run["trajectory"] if not e.startswith("plan:")]
    md += ["", "## 사건 순서(마지막 차례)", "", "`" + " → ".join(events) + "`", ""]
    return "\n".join(md)


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) < 3:
        print(__doc__)
        return 2
    src, out, ids = Path(args[0]), Path(args[1]), args[2:]
    runs: dict[str, dict] = {}
    for line in (src / "runs.jsonl").read_text(encoding="utf-8").splitlines():
        r = json.loads(line)
        if r["rep"] == 0:
            runs.setdefault(r["scenario"], r)
    trs: dict[str, dict] = {}
    for line in (src / "transcripts.jsonl").read_text(encoding="utf-8").splitlines():
        t = json.loads(line)
        if t["rep"] == 0:
            trs.setdefault(t["scenario"], t)
    out.mkdir(parents=True, exist_ok=True)
    for sid in ids:
        if sid not in runs or sid not in trs:
            print(f"없음: {sid}")
            return 1
        (out / f"{sid}.md").write_text(render(sid, runs[sid], trs[sid], src.name), encoding="utf-8")
    print(f"{len(ids)}개 → {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
