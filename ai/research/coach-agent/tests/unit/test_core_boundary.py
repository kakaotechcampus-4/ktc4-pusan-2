"""코어(coach)를 나중에 service 로 폴더째 옮길 수 있는지, 같은 요청에 같은 응답이 나오는지 검사한다.

- 패키지 안에서는 상대 import 만 쓴다. 패키지 경로가 바뀌어도 고칠 곳이 없게 하기 위해서다.
- 허용한 라이브러리만 쓴다: 계산만 하는 표준 모듈과 pydantic. 파일 · DB · 네트워크 · 환경변수는
  코어 밖이 맡고(research 에서는 coach_lab, 나중에는 service 와 BE), 시계 · 난수는 쓰지 않는다.
  시간은 요청의 t_ms 뿐이다 — 그래야 재생 결과와 배포 결과가 같다.
- 로그는 표준 logging 으로 남기기만 한다. 어디로 보낼지(핸들러 · 설정)는 service 가 정한다.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

CORE = Path(__file__).resolve().parents[2] / "src" / "coach"

# 코어가 쓰는 표준 모듈. 계산만 하는 모듈만 둔다. 새로 필요하면 그 모듈이
# 파일 · 네트워크 · 환경변수 · 시계 · 난수를 쓰지 않는지 확인하고 여기에 더한다
STDLIB = {
    "__future__", "abc", "bisect", "collections", "copy", "dataclasses", "enum", "functools",
    "hashlib", "heapq", "itertools", "json", "logging", "math", "operator", "re", "statistics",
    "typing",
}  # fmt: skip
THIRD_PARTY = {"pydantic"}
# 허용한 모듈 안에서도 설정 파일 · 출력 대상을 다루는 하위 모듈은 쓰지 않는다
FORBIDDEN_SUBMODULES = {"logging.config", "logging.handlers"}
FORBIDDEN_CALLS = {"print", "open", "input", "__import__", "exec", "eval"}

FILES = sorted(CORE.rglob("*.py"))


def _allowed(module: str) -> bool:
    if any(module == sub or module.startswith(f"{sub}.") for sub in FORBIDDEN_SUBMODULES):
        return False
    return module.split(".")[0] in STDLIB | THIRD_PARTY


def test_core_has_files():
    assert FILES, f"코어 파일이 없다: {CORE}"


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.relative_to(CORE).as_posix())
def test_core_imports(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    # 이 파일에서 상대 import 가 올라갈 수 있는 최대 단계. 넘으면 coach 패키지 밖을 가리킨다
    max_level = len(path.relative_to(CORE).parent.parts) + 1
    problems = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if not _allowed(alias.name):
                    problems.append(f"{node.lineno}: import {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            if node.level > max_level:
                problems.append(f"{node.lineno}: coach 패키지 밖을 가리키는 상대 import")
            elif node.level == 0:
                module = node.module or ""
                names = [f"{module}.{alias.name}" for alias in node.names]
                if not _allowed(module) or not all(_allowed(n) for n in names):
                    problems.append(f"{node.lineno}: from {module} import ... (상대 import 로)")
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in FORBIDDEN_CALLS
        ):
            problems.append(f"{node.lineno}: {node.func.id}() 호출")
    assert not problems, "\n".join(problems)
