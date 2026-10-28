"""코어(script_parser)가 서비스 · 연구 도구 어디에서 불려도 고칠 곳이 없는 순수 패키지인지 검사한다
(script-parser.md 4-3, architecture.md 8절).

- 패키지 안에서는 상대 import 만 쓴다. 패키지 경로가 바뀌어도 고칠 곳이 없게 하기 위해서다.
- 허용한 라이브러리만 쓴다: 계산만 하는 표준 모듈과 pydantic. 파일 · DB · 네트워크 · 환경변수 ·
  시계 · LLM 클라이언트는 쓰지 않는다. LLM 은 인자로 받고(`.invoke` 만), 만드는 것은 서비스 라우터 ·
  연구 폴더 `run.py` 다 — 가짜 LLM 으로 시험할 수 있게.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from pitch_coach_ai.features import script_parser

# 코어 패키지 파일 기준으로 찾는다 — 패키지 위치가 바뀌어도 경로를 고치지 않게
CORE = Path(script_parser.__file__).resolve().parent

# 코어가 쓰는 표준 모듈. 계산만 하는 모듈만 둔다. 새로 필요하면 그 모듈이
# 파일 · 네트워크 · 환경변수 · 시계 · 난수를 쓰지 않는지 확인하고 여기에 더한다
STDLIB = {
    "__future__", "collections", "dataclasses", "functools", "itertools", "logging", "re",
    "typing", "unicodedata",
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
    # 이 파일에서 상대 import 가 올라갈 수 있는 최대 단계. 넘으면 script_parser 패키지 밖을 가리킨다
    max_level = len(path.relative_to(CORE).parent.parts) + 1
    problems = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if not _allowed(alias.name):
                    problems.append(f"{node.lineno}: import {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            if node.level > max_level:
                problems.append(f"{node.lineno}: script_parser 패키지 밖을 가리키는 상대 import")
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
