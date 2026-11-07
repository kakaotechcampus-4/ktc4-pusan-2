"""서비스 단위 시험의 도우미를 연구 모듈에서 가져다 쓴다 (기능 문서 4-1 · 10-2).

- 무엇: 가상 기록(`fixtures`) · 예시 만들기(`example_builder`) · 가짜 모델(`fakes`) · 기록 판 만들기(`snapshots`)와
  턴 시험의 공통 값(`turn_tests`). 원본은 서비스 `ai/service/tests/unit/chat_agent/`에 하나만 둔다(코어 단위 시험이
  CI 에서 같은 도우미를 쓰기 때문이다).
- 어떻게: 그 폴더를 고유한 패키지 이름(`chat_agent_service_tests`)으로 읽는다. import 경로(`sys.path`)는 바꾸지 않으므로
  다른 `chat_agent` 이름을 가리지 않는다. 도우미끼리는 상대 import 라 이 이름으로도 그대로 돈다.
- 시험 전용 모듈(`fakes` · `snapshots` · `turn_tests`)은 처음 쓸 때 읽는다. 평가 · 가짜 BE · 예시 명령은 `fixtures` ·
  `example_builder`만 쓰므로 시험 도구(pytest) 없이 돈다.
- 쓰는 곳: 가짜 BE(`fake_be.py`) · 평가(`scenario.py` · `app.py`) · 예시 명령(`examples.py`)과 연구 시험(`tests/lab/`).
"""

from __future__ import annotations

import importlib
import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

SERVICE_UNIT_TESTS = (
    Path(__file__).resolve().parents[4] / "service" / "tests" / "unit" / "chat_agent"
)
PACKAGE = "chat_agent_service_tests"


def _package() -> ModuleType:
    pkg = sys.modules.get(PACKAGE)
    if pkg is None:
        spec = importlib.util.spec_from_file_location(
            PACKAGE,
            SERVICE_UNIT_TESTS / "__init__.py",
            submodule_search_locations=[str(SERVICE_UNIT_TESTS)],
        )
        assert spec is not None and spec.loader is not None, SERVICE_UNIT_TESTS
        pkg = importlib.util.module_from_spec(spec)
        sys.modules[PACKAGE] = pkg
        spec.loader.exec_module(pkg)
    return pkg


def _load(name: str) -> ModuleType:
    _package()
    return importlib.import_module(f"{PACKAGE}.{name}")


fixtures = _load("fixtures")
example_builder = _load("example_builder")

_LAZY = {"fakes": "fakes", "snapshots": "snapshots", "turn_tests": "test_turn"}


def __getattr__(name: str) -> Any:
    if name in _LAZY:
        mod = _load(_LAZY[name])
        globals()[name] = mod
        return mod
    raise AttributeError(name)


# fakes · snapshots · turn_tests 는 위 __getattr__ 가 처음 쓸 때 읽는다
__all__ = ["example_builder", "fixtures"]
