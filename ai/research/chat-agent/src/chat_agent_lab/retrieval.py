"""자료 검색 평가 — 정답 카드의 recall@k · MRR와 '자료 없음' 판정을 잰다 (기능 문서 6-10 '찾는 법', P-0008 5-2 · 5-4, 연구 전용).

실행(`ai/research/chat-agent`에서, 키워드 검색은 요금 없음)::

    .venv/bin/python -m chat_agent_lab.retrieval --corpus all --split dev
    .venv/bin/python -m chat_agent_lab.retrieval --corpus approved --split test --out <폴더>

- 문항: `evals/retrieval.json`. 문항 하나 = `{id, query, expected[], acceptable[], screen, kind(method · usage · none), split(dev · test), style(user · keyword)}`.
  기대 카드는 검색 코드와 따로(검색 코드를 보지 않고) 카드 본문만 보고 정했다. `none`은 어느 카드도 답을 뒷받침하지 않는 질문이다.
- 지표(문항마다 그 모드의 묶음에 실린 카드만 정답으로 센다 — 실리지 않은 카드를 겨냥한 문항은 '자료 없음'이 맞는 답이다)
  - recall@k: 정답 카드(expected) 중 하나라도 위 k개 안에 든 문항 비율. `acceptable`까지 넓힌 값(`recall@k_ok`)도 따로.
  - MRR: 첫 정답 카드 순위의 역수 평균(없으면 0).
  - none 정확도: 정답이 없는 문항에서 결과가 0건인 비율(자료가 없는데 카드를 내면 모델이 그 카드로 GUIDE 를 쓰려 한다).
  - 놓침: 정답이 있는 문항에서 결과가 0건인 비율('자료 없음'으로 잘못 판정 → 일반 조언으로 샘).
- 검색기는 바꿔 끼울 수 있다(`Searcher`): 키워드(`Corpus.search`)와 하이브리드를 같은 문항으로 비교한다. 임계값 · 가중치는 dev 로만
  고르고 test 는 고른 뒤 한 번 본다(과적합 확인).
- 고칠 때: 문항을 바꾸면 고르기를 다시 해야 하므로 dev · test 를 섞지 않는다. 시험은 tests/lab/test_retrieval.py.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pitch_coach_ai.features.chat_agent.corpus import Corpus

from . import scenario as scn

RETRIEVAL_FILE = Path(__file__).resolve().parents[2] / "evals" / "retrieval.json"
KS = (1, 3, 5)

#: 검색기: (묶음, 질의, 화면) → 순위대로 고른 source_id 목록(빈 목록 = 자료 없음)
Searcher = Callable[[Corpus, str, "str | None"], list[str]]


@dataclass(frozen=True)
class Item:
    id: str
    query: str
    expected: tuple[str, ...]
    acceptable: tuple[str, ...]
    screen: str | None
    kind: Literal["method", "usage", "none"]
    split: Literal["dev", "test"]
    style: Literal["user", "keyword"]


def load_items(path: Path = RETRIEVAL_FILE) -> list[Item]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    items = [
        Item(
            id=r["id"],
            query=r["query"],
            expected=tuple(r.get("expected") or ()),
            acceptable=tuple(r.get("acceptable") or ()),
            screen=r.get("screen"),
            kind=r["kind"],
            split=r["split"],
            style=r["style"],
        )
        for r in raw
    ]
    ids = [i.id for i in items]
    if len(ids) != len(set(ids)):
        raise ValueError("검색 문항 id 가 겹친다")
    for i in items:
        if (i.kind == "none") != (not i.expected):
            raise ValueError(f"{i.id}: kind none 이면 expected 가 비고, 아니면 차 있어야 한다")
    return items


def keyword_searcher(corpus: Corpus, query: str, screen: str | None) -> list[str]:
    return [h.card.source_id for h in corpus.search(query, screen=screen)]


def _rank(got: Sequence[str], want: set[str]) -> int | None:
    for n, sid in enumerate(got, start=1):
        if sid in want:
            return n
    return None


def evaluate(
    items: Sequence[Item], corpus: Corpus | None, searcher: Searcher = keyword_searcher
) -> dict[str, Any]:
    """문항마다 검색해 지표를 모은다. 묶음이 없으면(None) 모든 결과가 0건이다(서비스의 '자료 없음')."""
    served = {c.source_id for c in corpus.cards} if corpus is not None else set()
    rows: list[dict[str, Any]] = []
    for it in items:
        got = searcher(corpus, it.query, it.screen) if corpus is not None else []
        want = set(it.expected) & served
        ok = (set(it.expected) | set(it.acceptable)) & served
        rows.append(
            {
                "id": it.id,
                "kind": it.kind,
                "style": it.style,
                "has_answer": bool(want),
                "got": got[:5],
                "rank": _rank(got, want) if want else None,
                "rank_ok": _rank(got, ok) if ok else None,
            }
        )
    pos = [r for r in rows if r["has_answer"]]
    neg = [r for r in rows if not r["has_answer"]]

    def frac(n: int, d: int) -> dict[str, Any]:
        return {"num": n, "den": d, "value": round(n / d, 4) if d else None}

    out: dict[str, Any] = {"n": len(rows), "positives": len(pos), "negatives": len(neg)}
    for k in KS:
        out[f"recall@{k}"] = frac(sum(1 for r in pos if r["rank"] and r["rank"] <= k), len(pos))
        out[f"recall@{k}_ok"] = frac(
            sum(1 for r in pos if r["rank_ok"] and r["rank_ok"] <= k), len(pos)
        )
    out["mrr"] = round(sum(1 / r["rank"] for r in pos if r["rank"]) / len(pos), 4) if pos else None
    out["none_accuracy"] = frac(sum(1 for r in neg if not r["got"]), len(neg))
    out["missed_all"] = frac(sum(1 for r in pos if not r["got"]), len(pos))
    out["rows"] = rows
    return out


def _md(rep: dict[str, Any], title: str) -> str:
    def v(x: Any) -> str:
        return (
            "—"
            if x is None
            else (f"{x['value']} ({x['num']}/{x['den']})" if isinstance(x, dict) else str(x))
        )

    lines = [f"# 검색 평가 — {title}", "", "| 지표 | 값 |", "|---|---|"]
    for key in (
        "recall@1",
        "recall@3",
        "recall@5",
        "recall@3_ok",
        "mrr",
        "none_accuracy",
        "missed_all",
    ):
        lines.append(f"| {key} | {v(rep.get(key))} |")
    lines += ["", "## 놓친 문항", ""]
    for r in rep["rows"]:
        if r["has_answer"] and not (r["rank"] and r["rank"] <= 3):
            lines.append(f"- {r['id']} ({r['kind']}/{r['style']}): got {r['got']}")
        if not r["has_answer"] and r["got"]:
            lines.append(f"- {r['id']} (자료 없음인데 냄): got {r['got']}")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="자료 검색 평가(recall@k · 자료 없음 판정)")
    ap.add_argument("--corpus", default="all", choices=scn.CORPUS_MODES)
    ap.add_argument("--split", default="dev", choices=("dev", "test", "both"))
    ap.add_argument("--out", type=Path, default=None)
    a = ap.parse_args(argv)
    items = [i for i in load_items() if a.split == "both" or i.split == a.split]
    corpus = scn.build_corpus_for(None, a.corpus)  # type: ignore[arg-type]
    rep = evaluate(items, corpus)
    rep["info"] = {
        "corpus_mode": a.corpus,
        "corpus_version": corpus.version if corpus else "none",
        "split": a.split,
        "searcher": "keyword",
    }
    md = _md(rep, f"{a.corpus} · {a.split} · keyword")
    if a.out:
        a.out.mkdir(parents=True, exist_ok=True)
        (a.out / "retrieval.json").write_text(
            json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (a.out / "retrieval.md").write_text(md, encoding="utf-8")
    sys.stdout.write(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
