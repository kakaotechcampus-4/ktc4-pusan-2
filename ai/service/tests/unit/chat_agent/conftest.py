"""대화 에이전트 단위 시험 공통 설정 (연구 모듈 tests/conftest.py 와 같은 규칙).

- 조사 흐름(코드가 판단 초안의 대상을 먼저 읽기, `Loop._prefetch_calls`, 기능 문서 6-1)은 `@pytest.mark.prefetch`를 단 시험만 켠다.
  - 켜는 곳: 조사 흐름 시험(`test_prefetch.py`). 연구 모듈의 가짜 BE 통합 시험도 같은 표지를 쓴다.
  - 끄는 곳: 그 밖의 단위 시험, 그리고 켠 파일 안에서도 `@pytest.mark.no_prefetch`를 단 시험. 모델 호출 순서를 대본으로
    정해 두어(모델이 먼저 읽는 순서) 코드가 먼저 읽으면 대본이 어긋나는 시험이다.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _no_prefetch_unless_marked(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    node = request.node
    if node.get_closest_marker("prefetch") is None or node.get_closest_marker("no_prefetch"):
        from pitch_coach_ai.features.chat_agent.loop import Loop

        monkeypatch.setattr(Loop, "_prefetch_calls", lambda self: [])


@pytest.fixture(autouse=True)
def _reassess_targets_when_marked(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    """기준(`targets`)을 바꾸는 다시 평가는 서비스 기본에서 막는다(Issue #1194 R5(a), `reassess_targets=False`). 그 기능 자체
    (확인 카드 · 비율 변환 · 기준 문구)를 보는 시험은 `@pytest.mark.reassess_targets`를 달아 켠 채 본다."""
    if request.node.get_closest_marker("reassess_targets") is not None:
        from pitch_coach_ai.features.chat_agent.config import DEFAULT_CONFIG

        monkeypatch.setitem(DEFAULT_CONFIG.__dict__, "reassess_targets", True)
