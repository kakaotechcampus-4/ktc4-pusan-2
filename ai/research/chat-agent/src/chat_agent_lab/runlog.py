"""실행 기록 발췌 — 시나리오를 평가 실행기와 같은 가짜 BE 경로로 한 번 돌려, 에이전트가 무엇을 보고 무엇을 정했는지를 문서에 붙일 수 있게 쓴다(연구 전용).

시나리오마다 `<id>.md`(한국어 표)와 `<id>.jsonl`(원본 줄)을 쓴다. 표는 AI 호출 순서대로 스트림 줄(plan · tool_done · claim · final)을 요약하고,
그 호출의 결정 로그(`agent_decision`: 신호 · 사실 · 결정자 · 효과 · 단계 · 도구 번호)와 호출 사이의 BE 행동(승인 · 실행)을 잇는다.
서명 상태(`state`) 값은 어디에도 쓰지 않는다(`<서명 상태 생략>`). 시나리오는 합성 자료다.

실행(`ai/research/chat-agent`에서. 실제 모델은 `ai/.env`의 기본 묶음이고 요금이 든다)::

    .venv/bin/python -m chat_agent_lab.runlog --ids rep-save-goal --search hybrid --out reports/run-logs/
    .venv/bin/python -m chat_agent_lab.runlog --ids path-nc-script-alt --dir evals/path --fake --out /tmp/x
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import secrets
import sys
from datetime import date
from pathlib import Path
from typing import Any

from pitch_coach_ai.common.llm import tool_role
from pitch_coach_ai.features.chat_agent import prompts
from pitch_coach_ai.features.chat_agent.config import DEFAULT_CONFIG
from pitch_coach_ai.features.chat_agent.version import FEATURE_VERSION

from . import evals as ev
from . import scenario as scn
from .app import embedding_env, from_env, make_embed_query
from .fake_be import Run

REDACTED = "<서명 상태 생략>"


def redact(obj: Any) -> Any:
    """`state` 키의 값을 모두 바꾼다(서명 상태는 문서에 남기지 않는다). null 은 그대로 둔다."""
    if isinstance(obj, dict):
        return {
            k: (REDACTED if k == "state" and v is not None else redact(v)) for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [redact(x) for x in obj]
    return obj


def _cell(x: Any) -> str:
    s = "" if x is None else str(x)
    return s.replace("|", "\\|").replace("\n", "<br>")


def _row(*cols: Any) -> str:
    return "| " + " | ".join(_cell(c) for c in cols) + " |"


def _summarize_event(e: dict[str, Any]) -> tuple[str, str]:
    """(이벤트 이름, 요약 칸)."""
    name = str(e.get("event"))
    d = e.get("data") or {}
    if name == "plan":
        steps = d.get("plan") or []
        return name, "<br>".join(f"[{s.get('status')}] {s.get('step')}" for s in steps)
    if name == "tool_done":
        t = d.get("trace") or {}
        return name, f"{t.get('tool')} · {t.get('outcome')} · {t.get('result_summary')}"
    if name == "claim":
        c = d.get("claim") or {}
        return name, f"{c.get('kind')}: {c.get('text')}"
    return name, json.dumps(redact(d), ensure_ascii=False)[:300]


def _final_summary(final: dict[str, Any] | None, error: str | None) -> str:
    if final is None:
        return f"끝 줄 없음 (오류 {error})"
    types = [a.get("type") for a in final.get("actions") or []]
    return f"status={final.get('status')} · actions={types}"


def _decision_row(d: dict[str, Any]) -> str:
    return _row(
        d.get("signal"),
        json.dumps(d.get("facts"), ensure_ascii=False) if d.get("facts") is not None else "",
        d.get("decider"),
        d.get("effect"),
        d.get("step"),
        d.get("tool_seq"),
    )


def _action_row(rec: Any) -> str:
    a = rec.action
    result = rec.result.get("status") if isinstance(rec.result, dict) else None
    return _row(
        a.get("type"),
        a.get("autonomy"),
        rec.decision,
        "실행함" if rec.executed else "실행 안 함",
        result,
    )


def _sorted_events(run: Run, call_no: int) -> list[dict[str, Any]]:
    return sorted((e for e in run.events if e.get("call") == call_no), key=lambda e: e["event_seq"])


def render(
    ctx: ev._Ctx, meta: dict[str, Any], *, passed: bool | None
) -> tuple[str, list[dict[str, Any]]]:
    """(md 글, jsonl 줄 목록)."""
    sc = ctx.sc
    md: list[str] = [f"# 실행 기록 발췌 — {sc.id}", ""]
    md += [
        f"- 기능 버전: {meta['feature_version']}",
        f"- 프롬프트 버전: {meta['prompt_version']}",
        f"- 모델: {meta['model']}",
        f"- 검색 방식: {meta['search']}",
        f"- 실행일: {meta['date']}",
        f"- 채점 결과(참고): {'통과' if passed else '실패' if passed is False else '채점 안 함'}",
        "",
        "시나리오는 합성 자료이고, 서명 상태 값은 생략했다.",
        "",
        f"## 시나리오 `{sc.id}`",
        "",
        f"- 제목: {sc.title}",
    ]
    lines: list[dict[str, Any]] = [{"type": "meta", **meta, "scenario": sc.id}]
    for rec in ctx.turns:
        turn = sc.turns[rec.index] if rec.index < len(sc.turns) else None
        msg = (turn.message if turn else None) or ("(자동 목표 확인)" if rec.kind == "auto" else "")
        md += ["", f"### 사용자 말 {rec.index + 1}", "", f"> {msg}"]
        if rec.error:
            md += ["", f"실행 오류: {rec.error}"]
        lines.append({"type": "user_message", "turn": rec.index, "text": msg})
        for run in rec.runs:
            left = list(run.actions)
            for call in run.calls:
                md += ["", f"#### AI 호출 {call.number} (`{call.request_id}`)", ""]
                md += [_row("seq", "event", "내용"), _row("---", "---", "---")]
                for e in _sorted_events(run, call.number):
                    name, text = _summarize_event(e)
                    md.append(_row(e["event_seq"], name, text))
                    lines.append(
                        {
                            "type": "stream",
                            "turn": rec.index,
                            "call": call.number,
                            "seq": e["event_seq"],
                            "event": name,
                            "data": redact(e.get("data")),
                        }
                    )
                fin = call.final
                md.append(_row("-", "final", _final_summary(fin, call.error_code)))
                lines.append(
                    {
                        "type": "final",
                        "turn": rec.index,
                        "call": call.number,
                        "error_code": call.error_code,
                        "final": redact(
                            {
                                k: (fin or {}).get(k)
                                for k in ("status", "message", "actions", "plan", "state", "meta")
                            }
                        )
                        if fin
                        else None,
                    }
                )
                ds = ctx.decisions.get(call.request_id, [])
                if ds:
                    md += ["", "결정 로그(`agent_decision`)", ""]
                    md += [
                        _row("signal", "facts", "decider", "effect", "step", "tool_seq"),
                        _row("---", "---", "---", "---", "---", "---"),
                    ]
                    for d in ds:
                        md.append(_decision_row(d))
                        lines.append(
                            {
                                "type": "decision",
                                "turn": rec.index,
                                "call": call.number,
                                **redact(d),
                            }
                        )
                ids = {a.get("action_id") for a in (fin or {}).get("actions") or []}
                mine = [r for r in left if r.action.get("action_id") in ids]
                left = [r for r in left if r not in mine]
                if mine:
                    md += ["", "BE 행동(호출 뒤)", ""]
                    md += [
                        _row("행동", "자율", "승인 결정", "실행", "결과"),
                        _row("---", "---", "---", "---", "---"),
                    ]
                    for r in mine:
                        md.append(_action_row(r))
                        lines.append(
                            {
                                "type": "action",
                                "turn": rec.index,
                                "call": call.number,
                                "action": redact(r.action),
                                "decision": r.decision,
                                "executed": r.executed,
                                "result_status": r.result.get("status")
                                if isinstance(r.result, dict)
                                else None,
                            }
                        )
            if left:
                md += ["", "BE 행동(어느 호출에도 매지 못함)", ""]
                md += [
                    _row("행동", "자율", "승인 결정", "실행", "결과"),
                    _row("---", "---", "---", "---", "---"),
                ]
                md += [_action_row(r) for r in left]
    return "\n".join(md) + "\n", lines


def run_logs(
    scenarios: list[scn.Scenario],
    inner: ev.ToolLlm,
    secret: bytes,
    cfg: ev.RunnerConfig,
    out: Path,
    *,
    model: str | None,
) -> list[Path]:
    """시나리오마다 한 번 돌려 `<id>.md` · `<id>.jsonl` 을 쓴다. 쓴 md 경로를 돌려준다."""
    out.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for sc in scenarios:
        ctx = ev._Ctx(sc, inner, secret, cfg)
        for t in sc.turns:
            ctx.do_turn(t)
        try:
            passed: bool | None = ev.grade(ctx)[0]
        except Exception:  # noqa: BLE001  # 채점은 참고용이라 실패해도 기록은 쓴다
            passed = None
        meta = {
            "feature_version": FEATURE_VERSION,
            "prompt_version": prompts.PROMPT_VERSION,
            "model": model or getattr(inner, "model_name", None),
            "search": cfg.search,
            "date": date.today().isoformat(),
        }
        md, lines = render(ctx, meta, passed=passed)
        p = out / f"{sc.id}.md"
        p.write_text(md, encoding="utf-8", newline="\n")
        (out / f"{sc.id}.jsonl").write_text(
            "".join(json.dumps(x, ensure_ascii=False) + "\n" for x in lines),
            encoding="utf-8",
            newline="\n",
        )
        written.append(p)
    return written


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="시나리오 실행 기록 발췌(연구 전용)")
    ap.add_argument("--ids", nargs="+", required=True)
    ap.add_argument("--dir", type=Path, default=None, help="시나리오 폴더(기본: evals/scenarios)")
    ap.add_argument("--search", default="keyword", choices=("keyword", "hybrid"))
    ap.add_argument("--fake", action="store_true", help="가짜 모델(요금 없음)")
    ap.add_argument("--bundle", default="OPENAI")
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args(argv)
    try:
        if a.dir is not None:
            sets, splits = ev.SETS["all"]
            scenarios = scn.load_scenarios(
                a.dir, sets=sets, splits=splits, ids=a.ids, check_counts=False
            )
        else:
            scenarios = scn.load_scenarios(ids=a.ids)
    except scn.ScenarioError as e:
        print("시나리오 검사 실패:", *e.problems, sep="\n  - ", file=sys.stderr)
        return 2
    if a.dir is not None:
        # 폴더에 있어도 요청한 id 만
        scenarios = [s for s in scenarios if s.id in set(a.ids)]
    embed_query = embedding_model = None
    dims = DEFAULT_CONFIG.embed_dimensions
    if a.search == "hybrid":
        emb = embedding_env()
        if emb is None:
            print("--search hybrid 에는 ai/.env 의 OPENAI_EMBEDDING_* 가 필요하다", file=sys.stderr)
            return 2
        embedding_model = emb.model
        dims = int(json.loads(scn.EMBEDDINGS_FILE.read_text(encoding="utf-8"))["dimensions"])
        embed_query = make_embed_query(emb, dims)
    cfg = ev.RunnerConfig(
        repeat=1,
        corpus_mode="approved",
        search=a.search,
        embed_query=embed_query,
        embedding_model=embedding_model,
        embed_dims=dims,
    )
    if a.fake:
        inner: ev.ToolLlm = ev.DryRunLlm()
        secret = secrets.token_bytes(32)
        model: str | None = "dry-run"
    else:
        app = from_env(bundle=a.bundle, role=dataclasses.replace(tool_role()), search="keyword")
        if app.llm is None:
            print("모델 설정(ai/.env)이 없습니다", file=sys.stderr)
            return 2
        inner = app.llm
        secret = app.secret or secrets.token_bytes(32)
        model = getattr(inner, "model_name", None)
    for p in run_logs(scenarios, inner, secret, cfg, a.out, model=model):
        print(f"썼다: {p}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
