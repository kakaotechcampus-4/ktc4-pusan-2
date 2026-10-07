"""코어(script_coverage)를 나중에 service 로 폴더째 옮길 수 있는지 검사한다.

- 패키지 안에서는 상대 import 만 쓴다. 패키지 경로가 바뀌어도 고칠 곳이 없게 하기 위해서다.
- 허용한 라이브러리만 쓴다. 파일 · DB · 네트워크 · 환경변수 · LLM 클라이언트는 코어 밖이 맡는다
  (research 에서는 coverage_lab, 나중에는 service).
- script_analysis 와 stt_evaluation 은 서로 import 하지 않는다.
- shared 는 둘 중 어느 쪽도 import 하지 않는다.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

CORE = Path(__file__).resolve().parents[2] / "src" / "script_coverage"

THIRD_PARTY = {"pydantic", "kiwipiepy", "sklearn", "numpy", "scipy"}
FORBIDDEN_STDLIB = {
    "sqlite3", "os", "pathlib", "shutil", "socket", "subprocess", "urllib", "http", "io", "glob",
}  # fmt: skip
ALLOWED = (set(sys.stdlib_module_names) - FORBIDDEN_STDLIB) | THIRD_PARTY | {"__future__"}

# 영역 → import 하면 안 되는 영역
FORBIDDEN_AREAS = {
    "shared": {"script_analysis", "stt_evaluation"},
    "script_analysis": {"stt_evaluation"},
    "stt_evaluation": {"script_analysis"},
}

FILES = sorted(CORE.rglob("*.py"))


def _module_parts(path: Path) -> list[str]:
    parts = list(path.relative_to(CORE).with_suffix("").parts)
    return parts[:-1] if parts[-1] == "__init__" else parts


def _resolve_relative(path: Path, node: ast.ImportFrom) -> list[str]:
    """상대 import 가 가리키는 코어 안의 모듈 경로 (패키지 이름 제외)."""
    package = list(path.relative_to(CORE).parent.parts)
    base = package[: len(package) - (node.level - 1)] if node.level > 1 else package
    return base + (node.module.split(".") if node.module else [])


def test_core_has_files():
    assert FILES, f"코어 파일이 없다: {CORE}"


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.relative_to(CORE).as_posix())
def test_core_imports(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    area = _module_parts(path)[0] if len(_module_parts(path)) > 1 else None
    problems = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top = alias.name.split(".")[0]
                if top not in ALLOWED:
                    problems.append(f"{node.lineno}: import {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0:
                top = (node.module or "").split(".")[0]
                if top not in ALLOWED:
                    problems.append(
                        f"{node.lineno}: from {node.module} import ... (상대 import 를 쓸 것)"
                    )
                continue
            target = _resolve_relative(path, node)
            if target and area and target[0] in FORBIDDEN_AREAS.get(area, set()):
                problems.append(f"{node.lineno}: {area} 가 {target[0]} 를 import 한다")
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in {"print", "open", "input"}
        ):
            problems.append(f"{node.lineno}: {node.func.id}() 호출")
    assert not problems, "\n".join(problems)
