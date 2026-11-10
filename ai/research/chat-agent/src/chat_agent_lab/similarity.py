"""유사도 검사 — 처음 보는(독립) 시나리오 세트가 기존 문항의 변형이 아닌지 본다 (기능 문서 13-5, 연구 전용).

실행(`ai/research/chat-agent`에서)::

    .venv/bin/python -m chat_agent_lab.similarity --new <새 폴더> --against evals/scenarios [--against <폴더> ...] \\
        [--threshold 0.45] [--out <파일>]

- 대표 글 = 시나리오의 차례 `message`를 이어 붙인 것(공백 · 문장 부호를 없애고 소문자). 비교 = 글자 3-그램 자카드.
- 점수가 threshold 이상이면 FLAG. 점수가 threshold-0.15 이상이고 `fixtures` 구조 지문(피치 수 · 피치별 Take 수 · 리뷰 덮어쓰기 칸 ·
  목표 유무 · 화면 이름 · 장애 종류)이 같으면 이것도 FLAG 로 표시한다.
- 출력에는 시나리오 글을 넣지 않는다: 새 문항마다 `id · 가장 비슷한 기존 id · 점수 · 판정`과 끝의 PASS / FLAG 수만.
- 종료 코드: FLAG 가 있으면 1, 없으면 0. 폴더의 JSON(`schema.json` 제외) 중 차례 칸(`turns`)이 있는 객체가 시나리오다.
  다음이면 비교하지 않고 2 다(빈 · 틀린 비교를 PASS 로 끝내지 않는다): 읽지 못한 파일(JSON 이 아니거나 객체가 아님), id 가
  `^[a-z0-9][a-z0-9-]*$` 꼴이 아님, 같은 id 가 한 폴더 안이나 폴더 사이(새 쪽 · 기존 쪽 모두)에 둘 이상, 차례 형식이 틀림(`turns`가
  비지 않은 객체 목록이고 차례마다 글이 있는 `message`와 `auto_goal_check: true` 중 정확히 하나 — scenario.py 의 차례 규칙), 새 쪽 ·
  기존 쪽에 시나리오가 하나도 없음. 그래서 글이 있는 `turns[].message`가 하나도 없는 문항은 자동 목표 확인 차례만 있는 문항뿐이다:
  비교할 글이 없어 점수가 0 이므로, 결과 끝에 그 수를 따로 적는다(작성 · 검수에서 따로 본다).
- 문제 출력에도 시나리오 글을 넣지 않는다: 파일 이름과 형식이 맞는 id 만 쓰고, 형식이 틀린 id 는 `<invalid-id-N>`으로 가린다.
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
#: 시나리오 id 형식(`evals/scenarios/schema.json`의 id 패턴과 같다)
ID_RE = re.compile(r"[a-z0-9][a-z0-9-]*")


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


def load_dir(folder: Path) -> tuple[list[dict[str, Any]], list[str]]:
    """폴더의 시나리오 JSON(`schema.json` 제외)을 파일 이름 순서대로 읽는다. 돌려주는 값: (시나리오, 읽지 못한 파일 이름).

    JSON 이 아니거나 객체가 아니면 읽지 못한 파일이다. 차례 칸(`turns`)이 없는 객체는 시나리오가 아니므로 담지 않는다. 차례 칸이
    있으면 형식이 틀려도 담는다(`validate`가 문제로 센다)."""
    out: list[dict[str, Any]] = []
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
        if "turns" in d:
            out.append(d)
    return out, bad


def _turn_ok(t: Any) -> bool:
    """차례 하나가 scenario.py 의 차례 규칙대로인가: 객체이고, 글이 있는 `message`와 `auto_goal_check: true` 중 정확히 하나."""
    if not isinstance(t, dict):
        return False
    msg, auto = t.get("message"), t.get("auto_goal_check") is True
    if msg is None:
        return auto
    return isinstance(msg, str) and bool(msg.strip()) and not auto


def turns_ok(turns: Any) -> bool:
    """차례가 비지 않은 목록이고 차례마다 형식이 맞는가(`_turn_ok`). 글이 있는 `message`가 하나도 없으면 자동 목표 확인 차례만 있는
    문항이다(형식은 맞지만 비교할 글이 없다, `compare`의 `textless`)."""
    return isinstance(turns, list) and bool(turns) and all(_turn_ok(t) for t in turns)


def validate(
    sides: Sequence[list[dict[str, Any]]],
) -> tuple[list[dict[str, dict[str, Any]]], list[str]]:
    """쪽(새 쪽, 기존 폴더들)마다 id → 시나리오와 문제 목록. 문제: id 형식이 아님(`<invalid-id-N>`으로 가림) · 모든 쪽을 통틀어
    겹친 id · 차례 형식이 틀렸거나 글이 있는 `message`가 없음. 문제 글에는 시나리오 글을 넣지 않는다."""
    problems: list[str] = []
    seen: set[str] = set()
    dups: set[str] = set()
    invalid = 0
    out: list[dict[str, dict[str, Any]]] = []
    for side in sides:
        got: dict[str, dict[str, Any]] = {}
        for sc in side:
            raw = sc.get("id")
            if isinstance(raw, str) and ID_RE.fullmatch(raw):
                sid = raw
                if sid in seen and sid not in dups:
                    dups.add(sid)
                    problems.append(f"id 가 겹친다: {sid}")
                seen.add(sid)
            else:
                invalid += 1
                sid = f"<invalid-id-{invalid}>"
                problems.append(f"id 형식이 아님: {sid}")
            if not turns_ok(sc.get("turns")):
                problems.append(f"차례 형식이 틀림(turns[].message · auto_goal_check): {sid}")
                continue
            got[sid] = sc
        out.append(got)
    return out, problems


def compare(
    new: dict[str, dict[str, Any]],
    old: dict[str, dict[str, Any]],
    threshold: float = DEFAULT_THRESHOLD,
) -> list[dict[str, Any]]:
    """새 문항마다 가장 비슷한 기존 문항을 찾아 {id, nearest, score, verdict, textless} 로 돌려준다(글은 담지 않는다).
    `textless`: 비교할 글(차례 `message`)이 없는 문항(자동 목표 확인 차례만)."""
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
                "textless": not g,
            }
        )
    return rows


def render(rows: list[dict[str, Any]]) -> str:
    lines = ["id\t가장 비슷한 기존 id\t점수\t판정"]
    lines += [f"{r['id']}\t{r['nearest']}\t{r['score']:.3f}\t{r['verdict']}" for r in rows]
    flags = sum(r["verdict"] == "FLAG" for r in rows)
    lines.append(f"PASS {len(rows) - flags} · FLAG {flags}")
    textless = sum(bool(r.get("textless")) for r in rows)
    if textless:
        lines.append(f"글이 없어 점수 0 인 문항(자동 목표 확인 차례만): {textless}")
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
    sides: list[list[dict[str, Any]]] = []
    bad: list[str] = []
    for d in [a.new, *a.against]:
        got, bad_dir = load_dir(d)
        sides.append(got)
        bad += bad_dir
    checked, problems = validate(sides)
    new = checked[0]
    old: dict[str, dict[str, Any]] = {k: v for side in checked[1:] for k, v in side.items()}
    # 읽지 못한 파일 · 틀린 id · 겹친 id · 차례 글 없음이 있거나 한쪽이 비면 비교하지 않는다(빈 · 틀린 비교는 PASS 라 변형을
    # 놓친다). 파일 이름과 형식이 맞는 id 만 낸다
    problems = [f"읽지 못한 파일: {name}" for name in bad] + problems
    if not sides[0]:
        problems.append("새 쪽에 시나리오가 없다")
    if not any(sides[1:]):
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
