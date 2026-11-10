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


@pytest.fixture(autouse=True)
def _config_markers(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """Issue #1194 로 바뀐 서비스 기본값을 이전 동작으로 돌려 보는 시험 표지(서비스 tests/unit/chat_agent/conftest.py 와 같은 규칙).

    - `model_signals`: 리뷰 없음 · 기본 자료에 없음 · 기록 없음 신호를 모델에게 묻던 이전 동작(`code_signal_responses=False`).
      모델이 가져오기를 고르는 대본으로 승인 · 재개 · 한도 규칙을 보는 시험에 단다(코드가 가져오면 대본이 어긋난다).
    - `reassess_targets`: 기준(`targets`) 다시 평가를 켠다(서비스 기본은 막음, R5(a)). 그 기능 자체를 보는 시험에 단다."""
    from pitch_coach_ai.features.chat_agent.config import DEFAULT_CONFIG

    if request.node.get_closest_marker("model_signals") is not None:
        monkeypatch.setitem(DEFAULT_CONFIG.__dict__, "code_signal_responses", False)
    if request.node.get_closest_marker("reassess_targets") is not None:
        monkeypatch.setitem(DEFAULT_CONFIG.__dict__, "reassess_targets", True)
