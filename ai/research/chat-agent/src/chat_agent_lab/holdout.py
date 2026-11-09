"""독립 최종 평가 세트 도구 — 형식 검사 · 동결 · 대조 (기능 문서 13-5, 연구 전용).

- 왜: 독립 최종 평가 문항은 에이전트를 고치는 사람이 보지 않고 쓰고, 고치기 전에 동결해 최종 측정에서 한 번만 돈다(13-5).
  그래서 이 명령들은 **문항 내용(사용자 말 · 기대값 · 가상 기록 값)을 출력하지 않는다.** 파일 이름 · 검사 id · 위치 · 오류 종류 ·
  해시만 낸다. 고치는 사람이 이 명령을 돌려도 문항을 보지 않게 하기 위해서다.
- 명령
  - `check --dir <폴더>`: 시나리오 모양(pydantic) · 뜻(묶음 · 차례 · 템플릿 이름 · 가상 기록) 검사. 파일마다 `OK` 또는 `FAIL`과
    위치 · 오류 종류. 위치에는 스키마가 정한 칸 이름과 목록 번호만 쓰고, 작성자가 정한 사전 열쇠(Take id · 화면 이름 · 모르는 칸
    이름)는 `<key>`로 가린다. 개수 검사는 하지 않는다(세트 · 묶음 이름은 기존 `GROUPS`를 쓰되 개수는 이 세트의 동결 목록이 정한다).
  - `freeze --dir <폴더> --rules <규칙 파일> --out <동결 목록>`: 파일별 SHA-256 · 전체 해시 · 규칙 파일 해시 · 스키마 · 채점기 코드
    해시 · 지금 커밋 · 에이전트 판을 적은 동결 목록(JSON)을 쓴다. 내용은 담지 않는다.
  - `verify --dir <폴더> --manifest <동결 목록> --rules <규칙 파일>`: 지금 문항 · 규칙 · 채점기가 동결 목록과 같은지 본다. 동결 목록에
    규칙 해시(`rules_sha256`)가 있으면 `--rules`가 필수이고 해시가 같아야 한다. 다르면 종료 코드 1.
- 유사도(기존 문항의 변형인지)는 `similarity.py`가 본다(그쪽도 글을 출력하지 않는다).
- 고칠 때: 출력에 문항 글이 새지 않는지 `tests/lab/test_holdout.py`가 본다.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import typing
from pathlib import Path
from typing import Any

from pitch_coach_ai.features.chat_agent.version import FEATURE_VERSION
from pydantic import BaseModel, ValidationError

from . import scenario as scn

LAB = Path(__file__).resolve().parent
#: 동결 목록에 해시를 남기는 채점 · 실행 코드(evals.freeze_info 와 같은 목록 + 이 파일 · 유사도)
GRADER_FILES = (
    "grading.py",
    "scenario.py",
    "evals.py",
    "judge.py",
    "fake_be.py",
    "holdout.py",
    "similarity.py",
)

#: `_semantic` 문제 문장 → 내용 없는 코드. 앞에서부터 처음 맞는 것
_SEMANTIC_CODES = (
    ("파일 이름과 id", "ID_FILENAME"),
    ("세트에 없는 묶음", "GROUP"),
    ("보류(holdout)", "SPLIT"),
    ("check_id 가 겹친다", "CHECK_ID_DUP"),
    ("차례 수", "TURN_RANGE"),
    ("템플릿", "TEMPLATE"),
    ("fixtures.screens", "SCREEN"),
    ("피치 · Take id 가 겹친다", "ID_DUP"),
    ("대화 범위 피치", "SCOPE"),
    ("가상 기록을 만들 수 없다", "FIXTURES"),
)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _files(directory: Path) -> list[Path]:
    return sorted(p for p in directory.glob("*.json") if p.name != "schema.json")


def _semantic_code(problem: str) -> str:
    for needle, code in _SEMANTIC_CODES:
        if needle in problem:
            return code
    return "OTHER"


def _models_in(tp: Any) -> list[type[BaseModel]]:
    """형 표기 안의 pydantic 모델들(목록 · 사전 · 선택 · 합 형 안까지)."""
    if isinstance(tp, type) and issubclass(tp, BaseModel):
        return [tp]
    return [m for arg in typing.get_args(tp) for m in _models_in(arg)]


def _schema_field_names(model: type[BaseModel]) -> frozenset[str]:
    """시나리오 스키마(`scenario.Scenario`와 그 안의 모델)가 정한 칸 이름 · 별칭 전체."""
    names: set[str] = set()
    seen: set[type[BaseModel]] = set()
    todo = [model]
    while todo:
        m = todo.pop()
        if m in seen:
            continue
        seen.add(m)
        for name, f in m.model_fields.items():
            names.add(name)
            if f.alias:
                names.add(f.alias)
            todo.extend(_models_in(f.annotation))
    return frozenset(names)


#: 오류 위치에 그대로 쓸 수 있는 이름(스키마가 정한 칸). 그 밖의 글(사전 열쇠 · 모르는 칸 이름 · 형 표지)은 `<key>`로 가린다
SCHEMA_FIELDS = _schema_field_names(scn.Scenario)


def _safe_loc(loc: tuple[Any, ...]) -> str:
    """오류 위치를 문항 글 없이: 목록 번호와 스키마 칸 이름만 남기고 나머지는 `<key>`."""
    return ".".join(str(x) if isinstance(x, int) or x in SCHEMA_FIELDS else "<key>" for x in loc)


def check_file(path: Path) -> list[str]:
    """파일 하나의 문제를 내용 없는 코드로 돌려준다(없으면 빈 목록)."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ["JSON"]
    try:
        sc = scn.Scenario.model_validate(data)
    except ValidationError as e:
        # 위치(loc)와 오류 종류만. 입력 값(input)과 메시지는 문항 글을 담을 수 있어 내지 않는다. 위치의 사전 열쇠
        # (Take id · 화면 이름)와 모르는 칸 이름도 작성자가 쓴 글이라 스키마 칸 이름이 아니면 가린다
        return [
            f"SCHEMA {_safe_loc(err.get('loc', ()))} {err.get('type', '?')}" for err in e.errors()
        ]
    out: list[str] = []
    for problem in scn._semantic(sc, path):  # noqa: SLF001 — 같은 연구 패키지의 뜻 검사를 그대로 쓴다
        code = _semantic_code(problem)
        # 검사 id · 차례 번호는 작성자가 붙인 식별자라 남긴다(문항 글 아님)
        ids = [c.check_id for c in sc.expected.checks if c.check_id in problem]
        out.append(code + (f" {ids[0]}" if ids else ""))
    return out


def check(directory: Path) -> int:
    bad = 0
    files = _files(directory)
    for p in files:
        problems = check_file(p)
        if problems:
            bad += 1
            print(f"{p.name} FAIL " + " | ".join(problems))
        else:
            print(f"{p.name} OK")
    print(f"파일 {len(files)}개 · 통과 {len(files) - bad} · 실패 {bad}")
    return 1 if bad or not files else 0


def _git_head() -> str | None:
    try:
        out = subprocess.run(
            ["git", "-C", str(LAB), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() or None


def manifest(directory: Path, rules: Path | None) -> dict[str, Any]:
    files = {p.name: _sha(p) for p in _files(directory)}
    combined = hashlib.sha256(
        "\n".join(f"{k} {v}" for k, v in sorted(files.items())).encode()
    ).hexdigest()
    return {
        "v": 1,
        "files": files,
        "count": len(files),
        "scenarios_sha256": combined,
        "rules_sha256": _sha(rules) if rules is not None else None,
        "schema_sha256": _sha(scn.SCHEMA_FILE) if scn.SCHEMA_FILE.exists() else None,
        "grader_sha256": {name: _sha(LAB / name) for name in GRADER_FILES if (LAB / name).exists()},
        "commit": _git_head(),
        "feature_version": FEATURE_VERSION,
    }


def freeze(directory: Path, rules: Path | None, out: Path) -> int:
    if check(directory) != 0:
        print("형식 검사를 통과하지 못해 동결하지 않는다")
        return 1
    m = manifest(directory, rules)
    out.write_text(json.dumps(m, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"동결: 문항 {m['count']}개 · scenarios_sha256 {m['scenarios_sha256']} · 목록 {out}")
    return 0


def verify(
    directory: Path, manifest_path: Path, *, rules: Path | None = None, grader: bool = True
) -> int:
    """지금 문항 · 규칙 · 채점기가 동결 목록과 같은가. `grader=False`면 문항 · 규칙만 본다(채점기를 일부러 고친 뒤 대조할 때).

    동결 목록에 규칙 해시(`rules_sha256`)가 있으면 `rules`(규칙 파일)가 있어야 하고 해시가 같아야 한다. 주지 않았거나 읽지 못하면
    다른 것으로 센다(규칙을 대조하지 않은 채 '같음'으로 끝내지 않는다)."""
    want = json.loads(manifest_path.read_text(encoding="utf-8"))
    now = manifest(directory, None)
    problems: list[str] = []
    for name in sorted(set(want["files"]) | set(now["files"])):
        if want["files"].get(name) != now["files"].get(name):
            problems.append(f"문항 다름: {name}")
    if want.get("rules_sha256") is not None:
        if rules is None:
            problems.append(
                "규칙 대조 못 함: 동결 목록에 규칙 해시가 있다(--rules 로 규칙 파일을 준다)"
            )
        elif not rules.is_file():
            problems.append("규칙 대조 못 함: 규칙 파일을 읽지 못했다")
        elif _sha(rules) != want["rules_sha256"]:
            problems.append("규칙 다름")
    if grader:
        for name, sha in (want.get("grader_sha256") or {}).items():
            if now["grader_sha256"].get(name) != sha:
                problems.append(f"채점 · 실행 코드 다름: {name}")
        if want.get("schema_sha256") != now.get("schema_sha256"):
            problems.append("스키마 다름")
    for p in problems:
        print(p)
    print("동결 목록과 같음" if not problems else f"다른 곳 {len(problems)}개")
    return 1 if problems else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m chat_agent_lab.holdout", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check")
    c.add_argument("--dir", type=Path, required=True)
    f = sub.add_parser("freeze")
    f.add_argument("--dir", type=Path, required=True)
    f.add_argument("--rules", type=Path, default=None)
    f.add_argument("--out", type=Path, required=True)
    v = sub.add_parser("verify")
    v.add_argument("--dir", type=Path, required=True)
    v.add_argument("--manifest", type=Path, required=True)
    v.add_argument(
        "--rules",
        type=Path,
        default=None,
        help="동결한 규칙 파일(동결 목록에 규칙 해시가 있으면 필수)",
    )
    v.add_argument("--no-grader", action="store_true", help="문항 · 규칙만 대조")
    a = ap.parse_args(argv)
    if a.cmd == "check":
        return check(a.dir)
    if a.cmd == "freeze":
        return freeze(a.dir, a.rules, a.out)
    return verify(a.dir, a.manifest, rules=a.rules, grader=not a.no_grader)


if __name__ == "__main__":
    sys.exit(main())
