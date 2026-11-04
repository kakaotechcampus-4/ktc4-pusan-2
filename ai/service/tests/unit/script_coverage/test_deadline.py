"""요청 마감이 중간에 지날 때: 마감 전에 끝난 장은 평가 기준이 그대로 남고, 남은 장만 `LLM_TIMEOUT` 이다.

서비스의 LLM 객체(`common/llm.py` 의 `StructuredLlm`)는 마감까지 남은 시간이 호출 한 번의 시간 제한보다
짧으면 새 호출을 시작하지 않고 `LlmTimeout` 을 낸다. 코어는 그 장만 실패로 담는다(기능 문서 7 · 8절).
가짜 시계로 시간을 움직이고, 장 처리 순서가 정해지도록 동시 호출을 1개로 둔다.
"""

import threading
from typing import Any

import pytest

from pitch_coach_ai.common.llm import StructuredLlm
from pitch_coach_ai.features.script_coverage import core as core_module
from pitch_coach_ai.features.script_coverage.core import ScriptLLMs, analyze_script

from .fakes import DeckAwareLLM
from .shared.slides import SLIDES

TIMEOUT = 10.0  # 호출 한 번 시간 제한 (가짜 시계의 초)
NUMBERS = (4, 6, 7)


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0
        self._lock = threading.Lock()

    def __call__(self) -> float:
        with self._lock:
            return self.now

    def advance(self, seconds: float) -> None:
        with self._lock:
            self.now += seconds


class _Runnable:
    """LangChain Runnable 처럼 `.invoke(input, config)` 를 가진 가짜. 부를 때마다 시계를 1초 움직인다."""

    def __init__(self, stage: str, clock: _Clock) -> None:
        self.deck = DeckAwareLLM(stage)
        self.clock = clock

    def invoke(self, messages: Any, config: Any = None) -> Any:
        self.clock.advance(1.0)
        return self.deck.invoke(messages)


def _run(calls_before_deadline: int) -> Any:
    """마감 전에 `calls_before_deadline` 번만 호출을 시작할 수 있게 마감을 잡고 평가 기준을 만든다."""
    clock = _Clock()
    # k 번째 호출(0부터) 직전 시각은 now + k. 남은 시간이 TIMEOUT 이상인 호출만 시작한다
    deadline = clock.now + TIMEOUT + calls_before_deadline - 0.5
    llms = ScriptLLMs(
        **{
            stage: StructuredLlm(
                _Runnable(stage, clock),
                role=f"script_coverage.{stage}",
                timeout_seconds=TIMEOUT,
                max_retries=1,
                deadline=deadline,
                clock=clock,
            )
            for stage in ("semantic", "final", "unit")
        }
    )
    return analyze_script(
        {
            "pitch_id": "p-1",
            "script_version_id": "sv-3",
            "slides": [{"slide_number": n, "script": SLIDES[("가상대본1", n)]} for n in NUMBERS],
        },
        llms=llms,
        model="test-model",
    )


@pytest.fixture(autouse=True)
def _one_call_at_a_time(monkeypatch: pytest.MonkeyPatch) -> None:
    # 장마다 1차 분석 → 최종 결론 → 전달 단위 순서로, 한 장씩 차례로 부르게 한다
    monkeypatch.setattr(core_module, "RUBRIC_MAX_WORKERS", 1)


def _failed(res: Any) -> list[tuple[int, str]]:
    return [(f.slide_number, f.error.code) for f in res.failed]


def test_slides_finished_before_the_deadline_keep_their_rubrics() -> None:
    res = _run(calls_before_deadline=3)  # 4번 장의 세 단계까지
    assert [r.meta.slide_number for r in res.rubrics] == [4]
    assert _failed(res) == [(6, "LLM_TIMEOUT"), (7, "LLM_TIMEOUT")]


def test_a_slide_cut_in_the_middle_fails_as_a_whole() -> None:
    res = _run(calls_before_deadline=5)  # 6번 장은 1차 분석 · 최종 결론까지만
    assert [r.meta.slide_number for r in res.rubrics] == [4]
    assert _failed(res) == [(6, "LLM_TIMEOUT"), (7, "LLM_TIMEOUT")]


def test_every_slide_fits_before_the_deadline() -> None:
    res = _run(calls_before_deadline=9)
    assert [r.meta.slide_number for r in res.rubrics] == list(NUMBERS)
    assert res.failed == []
