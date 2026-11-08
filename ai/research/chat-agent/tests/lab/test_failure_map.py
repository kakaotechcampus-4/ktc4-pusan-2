"""실패 행 대응표 시험 — README '실패 행 대응표 (10-3)'가 기능 문서 7-3 ~ 7-8 의 AI 몫 행 · 시험 파일과 맞는지 본다 (기능 문서 10-3).

- 기능 문서 표: 7-3 ~ 7-8 의 `| L?-?? |` 행을 읽고, '시험' 열 앞의 `[층] (담당)`으로 AI 몫을 가린다(규칙은 `ai_owned`, README 설명과 같다).
- README 표: 행마다 층 · 시험(`경로::이름`, 여럿이면 ` · `로 잇는다) 또는 '미검증' · '평가셋' · 메모.
- 시험 이름은 연구 모듈 tests/ 와 서비스 ai/service/tests/ 아래 test_*.py 를 ast 로 읽어 모은 함수 이름과
  대조한다(가져와 실행하지 않는다). 연구 시험은 `tests/…`, 서비스 시험(옮긴 코어 시험 · 도구 호출 LLM 시험)은
  `ai/service/tests/…`로 적는다.
- 고칠 때: 기능 문서에 행이 늘거나 담당이 바뀌면 README 표에 그 행을 더한다. AI 몫 규칙을 바꾸면 README 설명도 같이 고친다.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from functools import cache
from pathlib import Path

MODULE = Path(__file__).resolve().parents[2]  # ai/research/chat-agent
README = MODULE / "README.md"
DOC = MODULE.parents[2] / "docs" / "ai" / "features" / "chat-agent.md"
ROOT = MODULE.parents[2]  # 저장소 루트
TESTS = MODULE / "tests"
SERVICE_TESTS = ROOT / "ai" / "service" / "tests"

SERVICE = "미검증"  # 서비스에서 아직 확인하지 않은 행(배포 사양 부하 시험 등)
EVAL = "평가셋"
#: 담당 표시가 없는 L2 ~ L6 행 중 처리가 AI 코드 · 설정인 행(README '실패 행 대응표'의 규칙 ③)
AI_WITHOUT_MARKER = frozenset({"L4-29", "L5-12", "L5-32", "L5-33"})
_ROW = re.compile(r"^\| (L[1-6]-\d\d) \|")
_TAG = re.compile(r"^\[([^\]]+)\]\s*(?:\(([^)]+)\))?")
_REF = re.compile(r"`((?:ai/service/)?tests/[\w/]+\.py)::(test_\w+)`")


@dataclass(frozen=True)
class DocRow:
    row_id: str
    layers: str
    owners: tuple[str, ...]


def _cells(line: str) -> list[str]:
    """표 한 줄의 칸. 칸 안의 `\\|`는 나누지 않는다."""
    return [c.strip() for c in re.split(r"(?<!\\)\|", line.strip())[1:-1]]


def ai_owned(row: DocRow) -> bool:
    """AI 몫인가: 담당 표시에 AI 가 있거나, 표시가 없는 L1 행이거나, 표시 없는 행 중 AI 코드 · 설정 행."""
    if row.owners:
        return "AI" in row.owners
    return row.row_id.startswith("L1-") or row.row_id in AI_WITHOUT_MARKER


@cache
def doc_rows() -> dict[str, DocRow]:
    lines = DOC.read_text(encoding="utf-8").splitlines()
    start = next(i for i, ln in enumerate(lines) if ln.startswith("## 7-3."))
    end = next(i for i, ln in enumerate(lines) if ln.startswith("## 7-9."))
    out: dict[str, DocRow] = {}
    for ln in lines[start:end]:
        m = _ROW.match(ln)
        if not m:
            continue
        tag = _TAG.match(_cells(ln)[-1])
        assert tag is not None, f"{m.group(1)}: 시험 열이 [층] 으로 시작하지 않는다"
        owners = tuple(o.strip() for o in (tag.group(2) or "").split("·") if o.strip())
        assert m.group(1) not in out, f"{m.group(1)} 이 두 번 나온다"
        out[m.group(1)] = DocRow(m.group(1), tag.group(1).strip(), owners)
    return out


@cache
def layer_counts() -> dict[str, int]:
    """7-2 '층 · 행 수' 표: 층 접두어 → 행 수."""
    lines = DOC.read_text(encoding="utf-8").splitlines()
    start = next(i for i, ln in enumerate(lines) if ln.startswith("## 7-2."))
    out: dict[str, int] = {}
    for ln in lines[start : start + 40]:
        c = _cells(ln) if ln.startswith("| L") else []
        if len(c) == 4 and re.fullmatch(r"L[1-6]", c[0]):
            out[c[0]] = int(c[3])
    return out


@dataclass(frozen=True)
class MapRow:
    row_id: str
    layers: str
    cell: str
    memo: str

    @property
    def refs(self) -> list[str]:
        return [f"{p}::{n}" for p, n in _REF.findall(self.cell)]


@cache
def table_rows() -> list[MapRow]:
    lines = README.read_text(encoding="utf-8").splitlines()
    start = next(i for i, ln in enumerate(lines) if ln.startswith("## 실패 행 대응표 (10-3)"))
    out: list[MapRow] = []
    for ln in lines[start + 1 :]:
        if ln.startswith("## "):
            break
        if _ROW.match(ln):
            c = _cells(ln)
            assert len(c) == 4, f"README 표 칸 수가 4가 아니다: {ln[:40]}"
            out.append(MapRow(c[0], c[1], c[2], c[3]))
    return out


@cache
def all_test_nodes() -> frozenset[str]:
    """연구 tests/ · 서비스 tests/ 아래 모든 시험 함수(`경로::이름`, 클래스 안의 것 포함)."""
    nodes: set[str] = set()
    files = [(f, f.relative_to(MODULE)) for f in TESTS.rglob("test_*.py")]
    files += [(f, f.relative_to(ROOT)) for f in SERVICE_TESTS.rglob("test_*.py")]
    for f, rel_path in files:
        rel = rel_path.as_posix()
        tree = ast.parse(f.read_text(encoding="utf-8"))
        # pytest 가 모으는 것만: 모듈 맨 위 함수와 Test 클래스의 메서드. skip · xfail 표시가 붙은 것은 빼다
        funcs = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
        for c in tree.body:
            if isinstance(c, ast.ClassDef) and c.name.startswith("Test"):
                funcs += [
                    n for n in c.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                ]
        for n in funcs:
            marks = " ".join(ast.unparse(d) for d in n.decorator_list)
            if n.name.startswith("test_") and "skip" not in marks and "xfail" not in marks:
                nodes.add(f"{rel}::{n.name}")
    return frozenset(nodes)


# ── 시험 ──────────────────────────────────────────────────────────────────


def test_doc_parser_reads_every_row_of_the_failure_tables() -> None:
    """읽은 행 수가 7-2 표의 층별 행 수와 같다(파서가 행을 놓치지 않는다)."""
    got: dict[str, int] = {}
    for rid in doc_rows():
        got[rid[:2]] = got.get(rid[:2], 0) + 1
    assert got == layer_counts()
    assert sum(got.values()) == 244


def test_every_ai_owned_row_is_in_the_table_once() -> None:
    ids = [r.row_id for r in table_rows()]
    assert len(ids) == len(set(ids)), "README 표에 같은 행이 두 번 있다"
    ai = {rid for rid, row in doc_rows().items() if ai_owned(row)}
    assert set(ids) == ai, {
        "빠진 행": sorted(ai - set(ids)),
        "AI 몫이 아닌 행": sorted(set(ids) - ai),
    }


def test_table_layers_match_the_doc() -> None:
    docs = doc_rows()
    wrong = [
        (r.row_id, r.layers, docs[r.row_id].layers)
        for r in table_rows()
        if r.row_id in docs and r.layers != docs[r.row_id].layers
    ]
    assert wrong == []


def test_every_referenced_test_exists() -> None:
    nodes = all_test_nodes()
    missing = [(r.row_id, ref) for r in table_rows() for ref in r.refs if ref not in nodes]
    assert missing == []


def test_each_row_names_tests_or_a_reason() -> None:
    """칸은 시험 이름만이거나 '미검증'(메모에 까닭) · '평가셋'(기능 문서가 평가셋 층으로 둔 행) 하나다."""
    docs = doc_rows()
    for r in table_rows():
        if r.refs:
            rest = _REF.sub("", r.cell).replace("·", "").strip()
            assert rest == "", f"{r.row_id}: 시험 이름 밖의 글이 있다({rest})"
        elif r.cell == SERVICE:
            assert r.memo, f"{r.row_id}: '{SERVICE}'의 까닭이 메모에 없다"
        elif r.cell == EVAL:
            assert EVAL in docs[r.row_id].layers, (
                f"{r.row_id}: 기능 문서가 평가셋 층으로 두지 않은 행이다"
            )
        else:
            raise AssertionError(f"{r.row_id}: 시험 · '{SERVICE}' · '{EVAL}' 중 하나가 아니다")
