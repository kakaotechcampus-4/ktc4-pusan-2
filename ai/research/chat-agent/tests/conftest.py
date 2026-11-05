"""시험 공통 설정.

- 조사 흐름(코드가 판단 초안의 대상을 먼저 읽기, `Loop._prefetch_calls`, 6-1)은 기본으로 끈다. 가짜 모델 시험 대부분이 모델
  호출 순서를 대본으로 정해 두었기 때문이다. 조사 흐름을 보는 시험은 `@pytest.mark.prefetch`를 단다(tests/unit/test_prefetch.py).
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _no_prefetch_unless_marked(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    if request.node.get_closest_marker("prefetch") is None:
        from chat_agent.loop import Loop

        monkeypatch.setattr(Loop, "_prefetch_calls", lambda self: [])
