"""자료 검색 평가 — 정답 카드의 recall@k · MRR와 '자료 없음' 판정을 잰다 (기능 문서 6-10 '찾는 법', P-0008 5-2 · 5-4, 연구 전용).

실행(`ai/research/chat-agent`에서, 키워드 검색은 요금 없음)::

    .venv/bin/python -m chat_agent_lab.retrieval --corpus all --split dev
    .venv/bin/python -m chat_agent_lab.retrieval --corpus approved --split test --out <폴더>
    .venv/bin/python -m chat_agent_lab.retrieval --search hybrid --corpus all --split dev
    .venv/bin/python -m chat_agent_lab.retrieval --search hybrid --embeddings outputs/r8/embeddings-512.json \
        --cosine-min 0.25 0.35 0.45

- 하이브리드(`--search hybrid`): `ai/.env` 의 임베딩 묶음으로 모든 문항 질문을 **요청 한 번**으로 임베딩한다. 결과는
  `outputs/r8/retrieval-query-embeddings.json` 에 (모델 · 차원 · 질문 해시) 열쇠로 모아 두어 다시 돌려도 요금이 들지 않는다.
  카드 벡터는 `--embeddings`(기본: 서비스가 읽는 `guides/embeddings.json`, 차원은 파일에서 읽음). `--cosine-min` 은 벡터 후보
  문턱을 덮어쓰고(여러 값을 주면 값마다 돌려 표로 모은다), dev 로 고른다.

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
import hashlib
import json
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pitch_coach_ai.features.chat_agent.config import DEFAULT_CONFIG, load_config
from pitch_coach_ai.features.chat_agent.corpus import Corpus

from . import scenario as scn
from .app import embedding_env

RETRIEVAL_FILE = Path(__file__).resolve().parents[2] / "evals" / "retrieval.json"
KS = (1, 3, 5)
#: 질문 임베딩 캐시(git 무시 폴더). 열쇠 = sha256(모델 | 차원 | 질문)
QUERY_CACHE = (
    Path(__file__).resolve().parents[2] / "outputs" / "r8" / "retrieval-query-embeddings.json"
)

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


def embed_queries(
    queries: Sequence[str],
    *,
    base_url: str,
    api_key: str,
    model: str,
    dims: int,
    cache: Path = QUERY_CACHE,
    embed_batch: Callable[..., list[list[float]]] | None = None,
) -> dict[str, list[float]]:
    """질문마다 임베딩(질문 → 벡터). 캐시에 없는 질문만 요청 한 번으로 묶어 부르고 캐시에 더한다."""
    if embed_batch is None:
        from pitch_coach_ai.common.embedding import embed_batch as _batch

        embed_batch = _batch

    def key(q: str) -> str:
        return hashlib.sha256(f"{model}|{dims}|{q}".encode()).hexdigest()

    store: dict[str, list[float]] = {}
    if cache.exists():
        store = json.loads(cache.read_text(encoding="utf-8"))
    missing = sorted({q for q in queries if key(q) not in store})
    if missing:
        vecs = embed_batch(
            base_url=base_url, api_key=api_key, model=model, dims=dims, texts=missing
        )
        for q, v in zip(missing, vecs, strict=True):
            store[key(q)] = v
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(store, separators=(",", ":")), encoding="utf-8", newline="\n")
    return {q: store[key(q)] for q in queries}


def hybrid_searcher(vectors: dict[str, list[float]]) -> Searcher:
    """하이브리드 검색기: 질문 벡터(`embed_queries`)를 묶음의 `search` 에 넘긴다. 묶음에 카드 벡터가 있어야 한다."""

    def search(corpus: Corpus, query: str, screen: str | None) -> list[str]:
        return [
            h.card.source_id
            for h in corpus.search(query, screen=screen, query_vector=vectors[query])
        ]

    return search


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
    ap.add_argument("--search", default="keyword", choices=("keyword", "hybrid"))
    ap.add_argument(
        "--embeddings",
        type=Path,
        default=None,
        help="hybrid 의 카드 벡터 파일(기본: guides/embeddings.json)",
    )
    ap.add_argument(
        "--cosine-min",
        type=float,
        nargs="+",
        default=None,
        help="hybrid 벡터 후보 문턱(기본: 설정값). 여러 값이면 값마다 돌려 표로 모은다",
    )
    ap.add_argument("--out", type=Path, default=None)
    a = ap.parse_args(argv)
    items = [i for i in load_items() if a.split == "both" or i.split == a.split]
    corpus = scn.build_corpus_for(None, a.corpus)  # type: ignore[arg-type]
    info: dict[str, Any] = {
        "corpus_mode": a.corpus,
        "corpus_version": corpus.version if corpus else "none",
        "split": a.split,
        "searcher": a.search,
    }
    runs: list[tuple[str, dict[str, Any]]] = []
    if a.search == "keyword" or corpus is None:
        runs.append(("keyword", evaluate(items, corpus)))
    else:
        emb = embedding_env()
        if emb is None:
            print("--search hybrid 에는 ai/.env 의 OPENAI_EMBEDDING_* 가 필요하다", file=sys.stderr)
            return 2
        file = a.embeddings or scn.EMBEDDINGS_FILE
        dims = int(json.loads(file.read_text(encoding="utf-8"))["dimensions"])
        try:
            vec_corpus = scn.with_search_vectors(corpus, model=emb.model, dims=dims, path=file)
        except ValueError as e:
            print(e, file=sys.stderr)
            return 2
        assert vec_corpus is not None
        vectors = embed_queries(
            [i.query for i in items],
            base_url=emb.base_url,
            api_key=emb.api_key,
            model=emb.model,
            dims=dims,
        )
        info.update(
            {"embedding_model": emb.model, "embed_dims": dims, "embeddings_file": file.name}
        )
        for cm in a.cosine_min or [DEFAULT_CONFIG.hybrid_cosine_min]:
            tuned = vec_corpus.with_cfg(load_config(hybrid_cosine_min=cm))
            runs.append((f"hybrid cos>={cm}", evaluate(items, tuned, hybrid_searcher(vectors))))
    md = ""
    for label, rep in runs:
        rep["info"] = {**info, "label": label}
        md += _md(rep, f"{a.corpus} · {a.split} · {label}")
    if len(runs) > 1:
        md += _sweep_md(runs)
    if a.out:
        a.out.mkdir(parents=True, exist_ok=True)
        (a.out / "retrieval.json").write_text(
            json.dumps(
                [r for _, r in runs] if len(runs) > 1 else runs[0][1], ensure_ascii=False, indent=2
            ),
            encoding="utf-8",
        )
        (a.out / "retrieval.md").write_text(md, encoding="utf-8")
    sys.stdout.write(md)
    return 0


def _sweep_md(runs: list[tuple[str, dict[str, Any]]]) -> str:
    def v(rep: dict[str, Any], k: str) -> str:
        x = rep.get(k)
        return "—" if x is None else str(x["value"] if isinstance(x, dict) else x)

    keys = ("recall@1", "recall@3", "recall@5", "mrr", "none_accuracy", "missed_all")
    lines = ["# 문턱 비교", "", "| 검색 | " + " | ".join(keys) + " |", "|---|" + "---|" * len(keys)]
    for label, rep in runs:
        lines.append(f"| {label} | " + " | ".join(v(rep, k) for k in keys) + " |")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
