"""고친 부분 재검증(8회차) 지적 시험 — 나열한 Take 번호 · 할 수 있는 일 질문 · 연습 부정 · 임베딩 자리 포화."""

from __future__ import annotations

import threading
from typing import Any

import openai
import pytest

from pitch_coach_ai.common import embedding as emb
from pitch_coach_ai.features.chat_agent import templates
from pitch_coach_ai.features.chat_agent.checks import named_take_numbers
from pitch_coach_ai.features.chat_agent.schemas import FinalData

from . import fakes as F

T = templates.SystemText


@pytest.mark.parametrize(
    ("text", "want"),
    [
        ("Take 4, 5 시선 알려 줘", {4, 5}),
        ("Take 4와 5 비교해 줘", {4, 5}),
        ("Take 4 · 5 · 6", {4, 5, 6}),
        ("Take 4와 5장 시선", {4}),  # 5장은 장 번호
        ("Take 5 3장", {5}),
    ],
)
def test_listed_take_numbers_are_all_read(text: str, want: set[int]) -> None:
    assert named_take_numbers(text) == want


def _run(msg: str, kind: str) -> tuple[FinalData, F.FakeLlm]:
    llm = F.FakeLlm(
        [F.plan(route="SMALL_TALK", steps=[], talk_kind=kind), F.done(), F.answer([]), F.answer([])]
    )
    _, _, data = F.run_turn(F.request(user_message=msg), llm)
    assert data is not None
    return FinalData.model_validate(data), llm


@pytest.mark.parametrize("msg", ["어떻게 도와줄 수 있어?", "뭘 알려줄 수 있어요?"])
def test_capability_questions_with_request_like_words_stay_small_talk(msg: str) -> None:
    fd, llm = _run(msg, "CAPABILITY")
    assert len(llm.calls) == 1
    assert fd.message is not None and fd.message.claims[0].text == T.TALK_CAPABILITY.value


def test_capability_kind_does_not_exempt_other_requests() -> None:
    _, llm = _run("점수 바꿔줄 수 있어?", "CAPABILITY")
    assert len(llm.calls) >= 2  # 점수 · 바꿔 → 조사로


@pytest.mark.parametrize("msg", ["오늘은 연습 안해요", "연습 못 해요"])
def test_practice_negation_variants_give_no_button(msg: str) -> None:
    fd, _ = _run(msg, "PRACTICE_INTENT")
    assert not fd.actions


def test_embedding_pool_full_returns_timeout_without_waiting(monkeypatch: Any) -> None:
    sem = threading.BoundedSemaphore(1)
    sem.acquire()  # 자리가 다 찼다
    monkeypatch.setattr(emb, "_SLOTS", sem)
    with pytest.raises(openai.APITimeoutError):
        emb.with_deadline(lambda texts, timeout: [[1.0]], ["q"], 2.0)
