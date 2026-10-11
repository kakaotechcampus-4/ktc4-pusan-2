"""코어(gaze)를 나중에 service 로 폴더째 옮길 수 있는지 검사한다.

- 패키지 안에서는 상대 import 만 쓴다. 패키지 경로가 바뀌어도 고칠 곳이 없게 하기 위해서다.
- 허용한 라이브러리만 쓴다 (허용 목록). 파일 · 네트워크 · 환경변수 · 프로세스 · 로그는 코어 밖이 맡는다.
- 연구용 코드(gaze_lab)를 import 하지 않는다. import 방향은 gaze_lab → gaze 한 방향뿐이다.
- 상대 import 는 패키지 안에서만 (``from .x``). ``from ..`` 는 패키지 밖으로 나간다.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

CORE = Path(__file__).resolve().parents[2] / "src" / "gaze"

# 코어가 쓰는 것만 허용한다. 새로 필요하면 여기에 더하고, 그 이유를 리뷰에서 본다
ALLOWED = {"__future__", "math", "dataclasses", "typing", "collections", "pydantic"}

FILES = sorted(CORE.rglob("*.py"))


def test_core_has_files():
    assert FILES, f"코어 파일이 없다: {CORE}"


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.relative_to(CORE).as_posix())
def test_core_imports(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    problems = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] not in ALLOWED:
                    problems.append(f"{node.lineno}: import {alias.name}")
        elif isinstance(node, ast.ImportFrom) and node.level > 1:
            problems.append(
                f"{node.lineno}: from {'.' * node.level}{node.module or ''} (패키지 밖)"
            )
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            if (node.module or "").split(".")[0] not in ALLOWED:
                problems.append(
                    f"{node.lineno}: from {node.module} import ... (상대 import 를 쓸 것)"
                )
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in {"print", "open", "input"}
        ):
            problems.append(f"{node.lineno}: {node.func.id}() 호출")
    assert not problems, "\n".join(problems)
