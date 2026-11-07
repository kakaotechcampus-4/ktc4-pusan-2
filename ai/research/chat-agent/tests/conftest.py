"""시험 공통 설정.

- 조사 흐름(코드가 판단 초안의 대상을 먼저 읽기, `Loop._prefetch_calls`, 6-1)은 `@pytest.mark.prefetch`를 단 시험만 켠다.
  - 켜는 곳: 가짜 BE 통합 시험(`tests/lab/test_fake_be*.py`, 파일 전체에 표지). 조사 흐름 시험은 서비스로 옮겼다
    (`ai/service/tests/unit/chat_agent/test_prefetch.py`, 같은 표지 규칙의 conftest 가 거기 있다).
    배포 기본값(`research_prefetch=True`)으로 승인 대기 · 이어 부르기 · 한도 · 지워진 원천 · 서명 상태를 돌리기 위해서다.
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
