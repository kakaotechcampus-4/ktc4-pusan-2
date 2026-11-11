"""'안 된다' 답의 대안 · 다음 걸음과 [이어서 확인] 반복 막기 시험 (Issue #1194 4-C · 4-E, `guidance.py`, 기능 문서 7-10).

- 핵심 고정 문장은 그대로 맨 앞이고, 그 뒤에 이번 요청의 기본 자료로 고른 대안 · 다음 걸음 문장(`SYSTEM`)이 붙는다.
- 직전 어시스턴트 메시지에 같은 핵심 문장이 있으면 선택지(예시 질문)를, 후보가 없으면 다음 걸음만.
- "이어서 확인해 줘"인데 직전 답과 같은 `PARTIAL` 안내가 또 나오면 `DONE` + 같은 이유 안내(버튼이 다시 붙지 않음).
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from pitch_coach_ai.features.chat_agent import guidance, templates
from pitch_coach_ai.features.chat_agent.schemas import FinalData

from . import fakes as F

T = templates.SystemText


def _history(*pairs: tuple[str, str]) -> list[dict[str, Any]]:
    return [
        {"role": role, "text": text, "truncated": False, "basis": None, "stale_reasons": []}
        for role, text in pairs
    ]


def _texts(fd: FinalData) -> list[str]:
    return [c.text for c in fd.message.claims]  # type: ignore[union-attr]


def _partial_checked(req: dict[str, Any]) -> FinalData:
    """답 문장이 검사로 모두 빠지고 고치기도 못 해 '답 일부를 확인하지 못해 …'로 끝나는 실행."""
    bad = F.claim("RECORD", "Take 5 3장 시선은 99%예요.", evidence_ids=["e-없음"])
    llm = F.FakeLlm([F.plan(), F.done()], answers=[F.answer([bad])], fixes=[F.fix({0: None})])
    _, _, data = F.run_turn(req, llm)
    return FinalData.model_validate(data)


def test_is_continue_ignores_spaces_and_end_marks() -> None:
    assert guidance.is_continue("이어서 확인해 줘")
    assert guidance.is_continue(" 이어서확인해줘. ")
    assert not guidance.is_continue("이어서 확인해 줘, 3장도")
    assert not guidance.is_continue(None)


def test_partial_without_history_stays_partial() -> None:
    fd = _partial_checked(F.request(user_message="이어서 확인해 줘"))
    assert fd.status == "PARTIAL"
    assert T.PARTIAL_CHECKED.value in _texts(fd)
    assert T.REPEAT_SAME_REASON.value not in _texts(fd)


def test_continue_with_the_same_partial_notice_ends_done() -> None:
    req = F.request(user_message="이어서 확인해 줘")
    req["history"]["messages"] = _history(
        ("user", "Take 5 3장 어땠어?"), ("assistant", T.PARTIAL_CHECKED.value)
    )
    fd = _partial_checked(req)
    assert fd.status == "DONE"
    texts = _texts(fd)
    assert texts[-2:] == [T.REPEAT_SAME_REASON.value, T.REPEAT_SPLIT.value]
    assert all(
        p.status != "TODO" for p in fd.plan
    )  # 남은 일이 없어 [이어서 확인] 버튼이 붙지 않는다


def test_continue_with_a_different_notice_stays_partial() -> None:
    req = F.request(user_message="이어서 확인해 줘")
    req["history"]["messages"] = _history(("assistant", T.LIMIT_PARTIAL.value))
    fd = _partial_checked(req)
    assert fd.status == "PARTIAL"
    assert T.REPEAT_SAME_REASON.value not in _texts(fd)


def test_same_partial_notice_but_other_message_stays_partial() -> None:
    req = F.request(user_message="Take 5 3장 다시 봐 줘")
    req["history"]["messages"] = _history(("assistant", T.PARTIAL_CHECKED.value))
    fd = _partial_checked(req)
    assert fd.status == "PARTIAL"


def _global_not_found(
    history: list[dict[str, Any]], pitches: list[dict[str, Any]], *, model_zero: bool = False
) -> FinalData:
    req = F.request(user_message="'창업 경진대회' 발표 기록 보여 줘")
    req["run"]["scope_pitch_id"] = None
    req["context"]["pitch"] = None
    req["context"]["loaded_takes"] = []
    req["context"]["pitches"] = pitches
    req["context"]["pitches_total"] = len(pitches)
    req["page"] = {"route": "/", "pitch_id": None, "take_id": None, "screen": None}
    req["history"]["messages"] = history

    def answer(r: Any) -> Any:
        if not model_zero:
            return F.answer([])
        # 모델이 '없음' 근거(이름 검색 0개)를 이은 기록 문장을 쓴다(실제 모델이 흔히 쓰는 꼴)
        eid = F.find_evidence(r, lambda o: o.get("value") == 0)
        return F.answer(
            [
                F.claim(
                    "RECORD",
                    "'창업 경진대회'라는 이름으로 찾은 피치는 0개예요.",
                    evidence_ids=[eid],
                )
            ]
        )

    llm = F.FakeLlm(
        [F.plan(), F.tools(("list_pitches", {"query": "창업 경진대회"})), F.done()],
        answers=[answer],
        fixes=[F.fix({})],
    )
    _, _, data = F.run_turn(req, llm)
    return FinalData.model_validate(data)


PITCHES = [
    {
        "pitch_id": "p-1",
        "title": "졸업 발표",
        "take_count": 3,
        "last_practiced_at": "2026-11-01T10:00:00+09:00",
        "last_review": None,
    },
    {
        "pitch_id": "p-2",
        "title": "투자 설명회",
        "take_count": 1,
        "last_practiced_at": "2026-11-08T10:00:00+09:00",
        "last_review": None,
    },
]


def test_pitch_not_found_offers_existing_titles_then_next_step() -> None:
    fd = _global_not_found([], PITCHES)
    assert _texts(fd) == [
        T.PITCH_NOT_FOUND.value,
        "지금 볼 수 있는 발표는 '투자 설명회' · '졸업 발표'예요.",  # 최근 연습 순
        T.NEXT_PICK_PITCH.value,
    ]
    assert fd.actions == [] and fd.message.cards == []  # type: ignore[union-attr]


@pytest.mark.parametrize("pitches", [PITCHES, []])
def test_model_zero_sentence_still_gets_alternatives_and_next_step(
    pitches: list[dict[str, Any]],
) -> None:
    """모델이 '이름으로 찾은 피치는 0개예요'(없음 근거만 이은 기록 문장)를 써도 그것은 답이 아니라 '안 된다'의 사실이라, 대안 ·
    다음 걸음을 붙인다(Issue #1194 독립 평가 — 전에는 이 문장을 답으로 세어 다음 걸음이 빠졌다)."""
    fd = _global_not_found([], pitches, model_zero=True)
    texts = _texts(fd)
    assert T.PITCH_NOT_FOUND.value in texts
    assert texts[-1] == (T.NEXT_PICK_PITCH.value if pitches else T.NEXT_FIRST_PITCH.value)


def test_name_outside_the_base_list_is_fetched_by_name_first() -> None:
    """기본 자료에 실린 피치(2개)보다 전체(3개)가 많으면 이름 검색 0개만으로 '찾을 수 없다'고 하지 않고 코드가 그 이름으로 가져오기를
    낸다(5-3, Issue #1194 독립 평가)."""
    req = F.request(user_message="'창업 경진대회' 발표 기록 보여 줘")
    req["run"]["scope_pitch_id"] = None
    req["context"]["pitch"] = None
    req["context"]["loaded_takes"] = []
    req["context"]["pitches"] = PITCHES
    req["context"]["pitches_total"] = 3
    req["page"] = {"route": "/", "pitch_id": None, "take_id": None, "screen": None}
    llm = F.FakeLlm(
        [F.plan(), F.tools(("list_pitches", {"query": "창업 경진대회"})), F.done()],
        answers=[F.answer([])],
    )
    _, _, data = F.run_turn(req, llm)
    fd = FinalData.model_validate(data)
    assert fd.status == "NEEDS_ACTION"
    assert [(a.type, a.args.get("name"), a.autonomy) for a in fd.actions] == [
        ("FETCH_PITCH", "창업 경진대회", "AUTO")
    ]
    assert fd.message is None or T.PITCH_NOT_FOUND.value not in _texts(
        fd
    )  # 가져오기 전에는 '찾을 수 없다'고 하지 않는다


def test_second_same_notice_offers_choices_instead_of_repeating() -> None:
    first = _global_not_found([], PITCHES)
    history = _history(
        ("user", "'창업 경진대회' 발표 기록 보여 줘"), ("assistant", "\n".join(_texts(first)))
    )
    fd = _global_not_found(history, PITCHES)
    texts = _texts(fd)
    assert texts[0] == T.PITCH_NOT_FOUND.value
    assert texts[1] == (
        f"{T.CHOICES_HEADER.value} '투자 설명회 최근 Take 어땠어?' · '졸업 발표 최근 Take 어땠어?'."
    )
    assert "지금 볼 수 있는 발표는" not in "\n".join(texts)  # 첫 안내의 대안을 되풀이하지 않는다


def test_no_candidates_gives_only_the_next_step() -> None:
    fd = _global_not_found([], [])
    assert _texts(fd)[1:] == [T.NEXT_FIRST_PITCH.value]
    again = _global_not_found(_history(("assistant", T.PITCH_NOT_FOUND.value)), [])
    assert T.CHOICES_HEADER.value not in "\n".join(_texts(again))
    assert _texts(again)[-1] == T.NEXT_FIRST_PITCH.value


def test_copula_follows_final_sound() -> None:
    assert guidance._copula("'졸업 발표'") == "예요"
    assert guidance._copula("'투자 설명회'") == "예요"
    assert guidance._copula("'창업 경진대회 본선'") == "이에요"
    assert guidance._copula("3") == "이에요" and guidance._copula("5") == "예요"


def test_pitches_without_finished_takes_are_not_offered() -> None:
    """분석이 끝난 Take 가 없는 피치는 대안 · 예시 질문에 넣지 않는다(볼 기록이 없다). 시간대가 섞여도 최근 순은 시각으로 정한다."""
    pitches = [
        *PITCHES,
        {
            "pitch_id": "p-3",
            "title": "새 발표",
            "take_count": 0,
            "last_practiced_at": None,
            "last_review": None,
        },
        {
            "pitch_id": "p-4",
            "title": "빈 설명회",
            "take_count": 0,
            "last_practiced_at": "2026-11-10T10:00:00+09:00",
            "last_review": None,
        },
        {
            "pitch_id": "p-5",
            "title": "오전 발표",
            "take_count": 1,
            "last_practiced_at": "2026-11-08T03:00:00Z",
            "last_review": None,
        },
    ]
    first = _global_not_found([], pitches)
    assert (
        _texts(first)[1] == "지금 볼 수 있는 발표는 '오전 발표' · '투자 설명회' · '졸업 발표'예요."
    )
    history = _history(
        ("user", "'창업 경진대회' 발표 기록 보여 줘"), ("assistant", "\n".join(_texts(first)))
    )
    again = "\n".join(_texts(_global_not_found(history, pitches)))
    assert "새 발표" not in again and "빈 설명회" not in again


def test_measurement_dispute_next_step_only_when_the_answer_has_none() -> None:
    """측정값 이의 문장은 답(판단 · 기록)이 있어도 다음 Take 확인 걸음을 붙인다. 답에 이미 '다음 Take · 연습'이 있으면 붙이지 않는다
    (Issue #1194 4-E, v1 회귀 f60-re-security-camera-dispute)."""
    from pitch_coach_ai.features.chat_agent.config import DEFAULT_CONFIG
    from pitch_coach_ai.features.chat_agent.evidence import EvidenceRegistry
    from pitch_coach_ai.features.chat_agent.schemas import Claim

    ctx = SimpleNamespace(registry=EvidenceRegistry("run"), cfg=DEFAULT_CONFIG)
    fixed = Claim(kind="SYSTEM", text=T.MEASUREMENT_FIXED.value)
    judged = Claim(kind="RECORD", text="Take 5에서는 시선 문제 구간이 0곳이에요.")
    got = guidance.follow_up(
        ctx, message="다시 평가해 줘", history_messages=[], claims=[fixed, judged], status="DONE"
    )
    assert [c.text for c in got.claims] == [T.NEXT_MEASURE_CHECK.value]
    said = Claim(kind="GENERAL", text="다음 연습에서는 카메라를 눈높이에 두고 다시 확인해 보세요.")
    got = guidance.follow_up(
        ctx,
        message="다시 평가해 줘",
        history_messages=[],
        claims=[fixed, judged, said],
        status="DONE",
    )
    assert got.claims == []


def test_code_name_fetch_is_dropped_when_a_pitch_fetch_already_happened() -> None:
    """모델이 같은 응답에서 이미 피치를 가져왔으면 코드의 이름 가져오기를 다시 내지 않는다(되풀이 막기, 코드 검수)."""
    from pitch_coach_ai.features.chat_agent.loop import LoopState

    class _L:
        st = LoopState.load({})

    from pitch_coach_ai.features.chat_agent.loop import Loop

    lp = _L()
    lp.st.executed.append({"type": "FETCH_PITCH", "status": "SUCCEEDED"})
    assert Loop._pitch_fetched(lp)  # type: ignore[arg-type]
