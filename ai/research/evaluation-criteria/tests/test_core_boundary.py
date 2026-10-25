"""코어(evaluation_criteria)를 나중에 service 로 폴더째 옮길 수 있는지 검사한다
(evaluation-criteria.md 4-3, architecture.md 8절).

- 패키지 안에서는 상대 import 만 쓴다.
- 허용한 라이브러리만 쓴다: 계산만 하는 표준 모듈과 pydantic. 파일 · 네트워크 · 환경변수 · 시계는 쓰지 않는다.
  LLM 은 인자로 받으므로 langchain 도 쓰지 않는다.
"""

import ast
from pathlib import Path

import pytest

CORE = Path(__file__).resolve().parents[1] / "src" / "evaluation_criteria"

# 코어가 쓰는 표준 모듈. 새로 필요하면 그 모듈이 파일 · 네트워크 · 환경변수 · 시계를 쓰지 않는지
# 확인하고 여기에 더한다
STDLIB = {"__future__", "logging", "typing"}
THIRD_PARTY = {"pydantic"}
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
    # 이 파일에서 상대 import 가 올라갈 수 있는 최대 단계. 넘으면 패키지 밖을 가리킨다
    max_level = len(path.relative_to(CORE).parent.parts) + 1
    problems = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if not _allowed(alias.name):
                    problems.append(f"{node.lineno}: import {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            if node.level > max_level:
                problems.append(f"{node.lineno}: 패키지 밖을 가리키는 상대 import")
            elif node.level == 0:
                module = node.module or ""
                if not _allowed(module):
                    problems.append(f"{node.lineno}: from {module} import ... (상대 import 로)")
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in FORBIDDEN_CALLS
        ):
            problems.append(f"{node.lineno}: {node.func.id}() 호출")
    assert not problems, "\n".join(problems)
