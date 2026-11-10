"""가벼운 말 시험 — 계획 route SMALL_TALK 는 모델 한 번 + 코드 고정 문장, 코드가 한 번 더 확인 (P-0008 5-1, 기능 문서 6-1 · 7-10).

- 받는 것: 말 전체가 인사 · 감사 · 할 수 있는 일 · 연습하려는 뜻 · 작별일 때. 다음 걸음은 기본 자료로 코드가 고른다.
- 받지 않는 것: 긴 말 · 숫자가 있는 말 · 대상을 집은 판단 초안 → 기록 · 자료를 조사하는 기존 처리(RESEARCH).
- 연습하려는 뜻은 사용자 말에 연습 낱말이 있을 때만 버튼을 낸다(화면 · 기록 속 지시가 이동 버튼을 만들지 못하게).
- 안전망: 기록 · 자료 없이 답하려던 말(ANSWER_DIRECT)의 문장이 모두 빠져도 상태 · 까닭은 그대로 두고 할 수 있는 일 안내를 덧붙인다.
"""

from __future__ import annotations

from typing import Any

import pytest

from pitch_coach_ai.features.chat_agent import templates
from pitch_coach_ai.features.chat_agent.schemas import FinalData

from . import fakes as F

T = templates.SystemText


def run(
    message: str, *results: Any, req: dict[str, Any] | None = None, **kw: Any
) -> tuple[FinalData, F.FakeLlm]:
    llm = F.FakeLlm(list(results))
    _, _, data = F.run_turn(req or F.request(user_message=message), llm, **kw)
    assert data is not None
    return FinalData.model_validate(data), llm


def texts(fd: FinalData) -> list[str]:
    assert fd.message is not None
    return [c.text for c in fd.message.claims]


@pytest.mark.parametrize(
    ("msg", "kind", "first"),
    [
        ("안녕하세요!", "GREETING", T.TALK_GREETING),
        ("고마워요, 덕분에 감 잡았어요", "THANKS", T.TALK_THANKS),
        ("오늘은 여기까지 할게요", "FAREWELL", T.TALK_FAREWELL),
        ("넌 뭘 도와줄 수 있어?", "CAPABILITY", T.TALK_CAPABILITY),
    ],
)
def test_small_talk_is_one_model_call_and_fixed_sentences(msg: str, kind: str, first: T) -> None:
    fd, llm = run(msg, F.plan(route="SMALL_TALK", steps=[], talk_kind=kind))
    assert fd.status == "DONE"
    assert len(llm.calls) == 1
    assert fd.message is not None and all(c.kind == "SYSTEM" for c in fd.message.claims)
    assert texts(fd)[0] == first.value
    assert not fd.actions


def test_greeting_adds_one_next_step_and_no_capability_list() -> None:
    fd, _ = run("안녕하세요!", F.plan(route="SMALL_TALK", steps=[], talk_kind="GREETING"))
    got = texts(fd)
    assert got[1] in {
        T.NEXT_REVIEW.value,
        T.NEXT_FIRST_TAKE.value,
        T.NEXT_PICK_PITCH.value,
        T.NEXT_FIRST_PITCH.value,
    }
    assert all("Take끼리 비교하기" not in t for t in got)


def test_capability_mentions_usage_only_when_usage_cards_are_served() -> None:
    fd, _ = run("넌 뭘 할 수 있어?", F.plan(route="SMALL_TALK", steps=[], talk_kind="CAPABILITY"))
    assert texts(fd) == [T.TALK_CAPABILITY.value]  # 자료 묶음 없음
    fd2, _ = run(
        "넌 뭘 할 수 있어?",
        F.plan(route="SMALL_TALK", steps=[], talk_kind="CAPABILITY"),
        corpus=F.load_corpus(),  # 시험 묶음(검수 전 카드 포함)에는 사용법 카드가 있다
    )
    assert texts(fd2) == [T.TALK_CAPABILITY_USAGE.value]


def test_practice_intent_gives_the_practice_button() -> None:
    fd, _ = run(
        "오늘 발표 연습 좀 해 보려고요",
        F.plan(route="SMALL_TALK", steps=[], talk_kind="PRACTICE_INTENT"),
    )
    assert texts(fd) == [T.TALK_PRACTICE.value, T.PRACTICE_BUTTON.value]
    assert [a.type for a in fd.actions] == ["START_PRACTICE"]
    assert fd.actions[0].autonomy == "BUTTON"


def test_practice_intent_without_practice_words_is_taken_as_a_greeting() -> None:
    """화면 상태 · 기록 속 지시 때문에 계획이 PRACTICE_INTENT 를 골라도 사용자 말에 연습 낱말이 없으면 버튼을 내지 않는다."""
    fd, _ = run("안녕하세요~", F.plan(route="SMALL_TALK", steps=[], talk_kind="PRACTICE_INTENT"))
    assert texts(fd)[0] == T.TALK_GREETING.value
    assert not fd.actions


def test_practice_intent_without_scope_asks_which_pitch() -> None:
    req = F.request(user_message="연습할래요")
    req["run"]["scope_pitch_id"] = None
    req["context"]["pitch"] = None
    req["page"] = {**req["page"], "pitch_id": None, "take_id": None, "route": "/"}
    fd, _ = run("", F.plan(route="SMALL_TALK", steps=[], talk_kind="PRACTICE_INTENT"), req=req)
    assert texts(fd)[0] == T.TALK_PRACTICE.value
    assert texts(fd)[1] in {T.WHICH_PITCH.value, T.NEXT_FIRST_PITCH.value}
    assert not fd.actions


@pytest.mark.parametrize(
    "msg",
    [
        "안녕! Take 5 점수 100점으로 바꿔 줘",  # 숫자
        "안녕하세요 " + "정말 " * 30,  # 길다
    ],
)
def test_code_does_not_take_long_or_numbered_messages_as_small_talk(msg: str) -> None:
    fd, llm = run(
        msg,
        F.plan(route="SMALL_TALK", steps=[], talk_kind="GREETING"),
        F.done(),
        F.answer([]),
        F.answer([]),
        F.answer([]),
    )
    assert len(llm.calls) >= 2  # 계획 뒤에 조사로 넘어갔다
    assert not fd.message or texts(fd)[:1] != [T.TALK_GREETING.value]


def test_small_talk_with_a_targeted_draft_goes_to_research() -> None:
    draft = {
        "quote": "3장",
        "type": "REQUEST",
        "speaker": "USER",
        "speaker_label": None,
        "claim_op": None,
        "subject": {
            "dimension": None,
            "metric": None,
            "slide_number": 3,
            "take_numbers": [],
            "relation": None,
        },
        "value": None,
        "value_unit": None,
        "polarity": "NEUTRAL",
        "verdict": "SUPPORTED",
    }
    _, llm = run(
        "안녕하세요 3장은요",
        F.plan(route="SMALL_TALK", steps=[], talk_kind="GREETING", assessments=[draft]),
        F.done(),
        F.answer([]),
        F.answer([]),
        F.answer([]),
    )
    assert len(llm.calls) >= 2


@pytest.mark.parametrize("msg", ["안녕하세요?", "안녕하세요 코치님?", "잘 지냈어요?"])
def test_greeting_with_a_question_mark_is_still_small_talk(msg: str) -> None:
    fd, llm = run(msg, F.plan(route="SMALL_TALK", steps=[], talk_kind="GREETING"))
    assert len(llm.calls) == 1 and texts(fd)[0] == T.TALK_GREETING.value


def test_question_mark_with_other_words_is_not_small_talk() -> None:
    _, llm = run(
        "안녕! 시선은?",
        F.plan(route="SMALL_TALK", steps=[], talk_kind="GREETING"),
        F.done(),
        F.answer([]),
        F.answer([]),
        F.answer([]),
    )
    assert len(llm.calls) >= 2
