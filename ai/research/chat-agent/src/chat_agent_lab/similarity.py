"""유사도 검사 — 처음 보는(독립) 시나리오 세트가 기존 문항의 변형이 아닌지 본다 (기능 문서 13-5, 연구 전용).

실행(`ai/research/chat-agent`에서)::

    .venv/bin/python -m chat_agent_lab.similarity --new <새 폴더> --against evals/scenarios [--against <폴더> ...] \\
        [--threshold 0.45] [--out <파일>]

- 대표 글 = 시나리오의 차례 `message`를 이어 붙인 것(공백 · 문장 부호를 없애고 소문자). 비교 = 글자 3-그램 자카드.
- 점수가 threshold 이상이면 FLAG. 점수가 threshold-0.15 이상이고 `fixtures` 구조 지문(피치 수 · 피치별 Take 수 · 리뷰 덮어쓰기 칸 ·
  목표 유무 · 화면 이름 · 장애 종류)이 같으면 이것도 FLAG 로 표시한다.
- 출력에는 시나리오 글을 넣지 않는다: 새 문항마다 `id · 가장 비슷한 기존 id · 점수 · 판정`과 끝의 PASS / FLAG 수만.
- 종료 코드: FLAG 가 있으면 1, 없으면 0. 기존 폴더는 형식 검사 없이 JSON 이면 읽는다(`schema.json` 제외).
  읽지 못한 파일(JSON 이 아니거나 객체가 아님)이 하나라도 있거나, 새 쪽 · 기존 쪽에 시나리오(차례 `turns`가 있는 객체)가 하나도
  없으면 비교하지 않고 2 다(빈 비교를 PASS 로 끝내지 않는다). 출력에는 파일 이름만 쓴다.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

DEFAULT_THRESHOLD = 0.45
#: 구조 지문이 같을 때 더 낮은 점수에서도 표시하는 폭
STRUCT_MARGIN = 0.15


def normalize(text: str) -> str:
    """공백 · 문장 부호를 없애고 소문자로 한다(글자 · 숫자만 남긴다)."""
    return re.sub(r"[\W_]+", "", text, flags=re.UNICODE).lower()


def grams(text: str, n: int = 3) -> set[str]:
    t = normalize(text)
    if len(t) < n:
        return {t} if t else set()
    return {t[i : i + n] for i in range(len(t) - n + 1)}


def jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 0.0
    return len(a & b) / len(a | b)


def representative(sc: dict[str, Any]) -> str:
    return " ".join(str(t.get("message") or "") for t in sc.get("turns") or [])


def fingerprint(sc: dict[str, Any]) -> tuple[Any, ...]:
    """fixtures 구조 지문: 피치 수 · 피치별 Take 수 · 리뷰(Take 덮어쓰기) 칸 · 목표 유무 · 화면 이름 · 장애 종류."""
    fx = sc.get("fixtures") or {}
    pitches = fx.get("pitches") or [{"preset": "sample"}]
    takes = tuple(
        (
            len(p["takes"]) if p.get("takes") is not None else -1,
            len(p.get("add_takes") or []),
            len(p.get("drop_takes") or []),
        )
        for p in pitches
    )
    review = tuple(
        sorted({k for p in pitches for ov in (p.get("take_overrides") or {}).values() for k in ov})
    )
    conv = fx.get("conversation") or {}
    faults = tuple(
        sorted({f.get("kind") for t in sc.get("turns") or [] for f in t.get("faults") or []})
    )
    return (
        len(pitches),
        takes,
        review,
        bool(conv.get("goal")),
        tuple(sorted((fx.get("screens") or {}).keys())),
        faults,
    )


def load_dir(folder: Path) -> tuple[dict[str, dict[str, Any]], list[str]]:
    """폴더의 시나리오 JSON(`schema.json` 제외)을 id → 내용으로 읽는다. 돌려주는 값: (시나리오, 읽지 못한 파일 이름).

    JSON 이 아니거나 객체가 아니면 읽지 못한 파일이다. 객체지만 차례(`turns`)가 비었으면 시나리오가 아니므로 담지 않는다."""
    out: dict[str, dict[str, Any]] = {}
    bad: list[str] = []
    for f in sorted(folder.glob("*.json")):
        if f.name == "schema.json":
            continue
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            bad.append(f.name)
            continue
        if not isinstance(d, dict):
            bad.append(f.name)
            continue
        if isinstance(d.get("turns"), list) and d["turns"]:
            out[str(d.get("id") or f.stem)] = d
    return out, bad


def compare(
    new: dict[str, dict[str, Any]],
    old: dict[str, dict[str, Any]],
    threshold: float = DEFAULT_THRESHOLD,
) -> list[dict[str, Any]]:
    """새 문항마다 가장 비슷한 기존 문항을 찾아 {id, nearest, score, verdict} 로 돌려준다(글은 담지 않는다)."""
    olds = [(i, grams(representative(d)), fingerprint(d)) for i, d in old.items()]
    rows = []
    for nid, d in new.items():
        g, fp = grams(representative(d)), fingerprint(d)
        best_id, best, best_fp = "-", 0.0, None
        for oid, og, ofp in olds:
            score = jaccard(g, og)
            if best_fp is None or score > best:
                best_id, best, best_fp = oid, score, ofp
        same_struct = best_fp is not None and best_fp == fp
        flag = best >= threshold or (same_struct and best >= threshold - STRUCT_MARGIN)
        rows.append(
            {
                "id": nid,
                "nearest": best_id,
                "score": round(best, 3),
                "verdict": "FLAG" if flag else "PASS",
            }
        )
    return rows


def render(rows: list[dict[str, Any]]) -> str:
    lines = ["id\t가장 비슷한 기존 id\t점수\t판정"]
    lines += [f"{r['id']}\t{r['nearest']}\t{r['score']:.3f}\t{r['verdict']}" for r in rows]
    flags = sum(r["verdict"] == "FLAG" for r in rows)
    lines.append(f"PASS {len(rows) - flags} · FLAG {flags}")
    return "\n".join(lines) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="독립 세트가 기존 문항의 변형인지 본다(글자 3-그램 자카드)"
    )
    ap.add_argument("--new", type=Path, required=True)
    ap.add_argument("--against", type=Path, action="append", required=True)
    ap.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD)
    ap.add_argument("--out", type=Path)
    a = ap.parse_args(argv)
    new, bad = load_dir(a.new)
    old: dict[str, dict[str, Any]] = {}
    for d in a.against:
        got, bad_old = load_dir(d)
        old.update(got)
        bad += bad_old
    # 읽지 못한 파일이 있거나 한쪽이 비면 비교하지 않는다(빈 비교는 모두 PASS 라 변형을 놓친다). 파일 이름만 낸다
    problems = [f"읽지 못한 파일: {name}" for name in bad]
    if not new:
        problems.append("새 쪽에 시나리오가 없다")
    if not old:
        problems.append("기존 쪽에 시나리오가 없다")
    if problems:
        sys.stdout.write("\n".join(problems) + "\n비교하지 않음\n")
        return 2
    rows = compare(new, old, a.threshold)
    text = render(rows)
    sys.stdout.write(text)
    if a.out is not None:
        a.out.write_text(text, encoding="utf-8", newline="\n")
    return 1 if any(r["verdict"] == "FLAG" for r in rows) else 0


if __name__ == "__main__":
    raise SystemExit(main())
