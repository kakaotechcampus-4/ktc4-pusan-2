"""일반 조언(`GENERAL`) 시험 — 자료를 찾은 뒤에만, 3문장 · 120자 · 금지 목록, 고정 안내와 함께 낸다 (기능 문서 6-9 · 6-10, L4-28).

검사(`check_claims`)는 문장 하나씩, 흐름은 가짜 모델과 실제 자료 묶음으로 본다.
"""

from __future__ import annotations

from typing import Any

import pytest

from chat_agent import templates
from chat_agent.checks import CheckId, check_claims
from chat_agent.config import DEFAULT_CONFIG
from chat_agent.evidence import EvidenceRegistry
from chat_agent.schemas import Claim, FinalData
from chat_agent_lab.app import load_corpus

from . import fakes as F
from .test_approval import SAVE as SAVE_GOAL
from .test_approval import drafts, payload_of, saved_result
from .test_stream_answer import Feeder, assert_prefix_invariant, claim_lines
from .test_turn import COMPARE, resume_request, roles

INTRO = templates.SystemText.GENERAL_INTRO.value
G1 = "중요한 문장 앞에서 잠깐 멈추면 청중이 따라오기 쉬워요."
G2 = "말 속도를 조금 늦추고 문장 끝을 또렷하게 맺어 보세요."
G3 = "청중 몇 사람과 차례로 눈을 맞추며 말하면 좋아요."
G4 = "첫 문장을 미리 입에 익혀 두면 시작이 편해져요."
#: 자료에 없는 질문으로 찾는다(자료 묶음에 맞는 카드가 없어 결과 0건)
NO_HIT = ("search_guides", {"query": "홍차 맛있게 우리기"})


def general(text: str, **kw: Any) -> Claim:
    return Claim(kind="GENERAL", text=text, **kw)


def failures(c: Claim, *, searched: bool = True) -> list[tuple[CheckId, bool]]:
    got = check_claims([c], registry=EvidenceRegistry("run-1"), guide_searched=searched)
    return [(f.check_id, f.unfixable) for f in got]


# ── 검사 (6-9 GENERAL) ──────────────────────────────────────────────────────


@pytest.mark.parametrize("text", [G1, G2, G3, G4, "긴장되면 숨을 천천히 내쉬고 시작해 보세요."])
def test_plain_general_advice_passes_after_a_search(text: str) -> None:
    """'말 속도' 같은 발표 요소 낱말과 권하는 말끝(~하면 좋아요)은 쓸 수 있다."""
    assert failures(general(text)) == []


def test_general_without_a_search_is_dropped_without_a_fix() -> None:
    assert failures(general(G1), searched=False) == [(CheckId.GENERAL_RULE, True)]


@pytest.mark.parametrize(
    "kw", [{"source_ids": ["g-01"]}, {"evidence_ids": ["e1"]}, {"assessment_id": "a1"}]
)
def test_general_linked_to_evidence_or_source_is_dropped_without_a_fix(kw: dict) -> None:
    assert (CheckId.GENERAL_RULE, True) in failures(general(G1, **kw))


def test_general_longer_than_the_limit_is_fixable() -> None:
    text = "가" * (DEFAULT_CONFIG.max_general_chars + 1)
    assert failures(general(text)) == [(CheckId.GENERAL_RULE, False)]
    assert failures(general("가" * DEFAULT_CONFIG.max_general_chars)) == []


@pytest.mark.parametrize(
    "text",
    [
        "3초쯤 쉬었다가 다음 장으로 넘어가 보세요.",  # 숫자
        "이번 발표에서는 문장을 짧게 끊어 보세요.",  # 기록 지시어
        "Take를 거듭하며 천천히 말해 보세요.",  # 기록 낱말(대소문자 무시)
        "청중 시선 비율을 높이려면 뒤쪽도 보세요.",  # 측정값 이름
        "연구에 따르면 쉼이 전달을 돕는다고 해요.",  # 출처 주장
        "목소리는 강점이니 그대로 살려 보세요.",  # 판정
        "잘하셨어요, 그 느낌으로 이어 가 보세요.",  # 칭찬
        "거의 다 왔으니 조금만 더 다듬어 보세요.",  # 완화
    ],
)
def test_general_forbidden_words_fail_general_rule(text: str) -> None:
    assert (CheckId.GENERAL_RULE, False) in failures(general(text))


def test_general_agreement_word_is_reported_once_as_general_rule() -> None:
    got = failures(general("좋아요, 그렇게 이어 가 보세요."))
    assert got == [(CheckId.GENERAL_RULE, False)]


@pytest.mark.parametrize(
    ("text", "cid"),
    [
        ("긴장 때문에 말이 빨라질 수 있어요.", CheckId.CAUSAL_CLAIM),
        ("예상 질문을 미리 적어 두세요.", CheckId.SCOPE_RULE),
        ("두 번 쉬고 시작해 보세요.", CheckId.NUMBER_FORMAT),
    ],
)
def test_common_checks_still_apply_to_general(text: str, cid: CheckId) -> None:
    assert cid in [c for c, _ in failures(general(text))]


# ── 흐름 (6-10 · L4-28) ─────────────────────────────────────────────────────


def _script(answer: Any, *, search: bool = True) -> list[Any]:
    research = [F.tools(NO_HIT)] if search else []
    return [F.plan(steps=["자료 찾기", "답 쓰기"]), *research, F.done(), answer]


_CORPUS = object()


def _run(
    answer: Any,
    *,
    search: bool = True,
    req: dict | None = None,
    corpus: Any = _CORPUS,
    **kw: Any,
) -> tuple[list[dict], dict, F.FakeLlm]:
    llm = F.FakeLlm(_script(answer, search=search), **kw)
    status, lines, data = F.run_turn(
        req or F.request(user_message="발표 전에 긴장을 풀려면 어떻게 해?"),
        llm,
        corpus=load_corpus() if corpus is _CORPUS else corpus,
        corpus_version="guides-3",
    )
    assert status == 200 and data is not None
    return lines, data, llm


def _texts(data: dict) -> list[tuple[str, str]]:
    return [(c["kind"], c["text"]) for c in data["message"]["claims"]]


def test_no_guide_answers_done_with_the_intro_and_at_most_three_general() -> None:
    """자료 0건 → 고정 안내 + 일반 조언 3문장(DONE). 넷째 문장과 일반 조언 뒤에 쓴 다른 종류는 버리고 진단에 남긴다."""
    lines, data, _ = _run(
        F.answer(
            [
                F.claim("GENERAL", G1),
                F.claim("GENERAL", G2),
                F.claim("RECORD", "기록 이야기는 일반 조언 뒤에 쓰면 버려요."),
                F.claim("GENERAL", G3),
                F.claim("GENERAL", G4),
            ],
            route="NO_GUIDE",
        ),
        stream_chunk=7,
    )
    fd = FinalData.model_validate(data)
    assert fd.status == "DONE"
    assert _texts(data) == [("SYSTEM", INTRO), ("GENERAL", G1), ("GENERAL", G2), ("GENERAL", G3)]
    assert all(c["evidence_ids"] == [] and c["source_ids"] == [] for c in data["message"]["claims"])
    assert_prefix_invariant(lines, data)  # 안내는 첫 일반 조언과 함께 문장 줄로 나갔다
    over = [
        s
        for s in fd.diagnostics.steps  # type: ignore[union-attr]
        if s.kind == "CHECK" and s.code == "GENERAL_RULE"
    ]
    assert [(s.detail.result, s.detail.claim_kind) for s in over] == [("DROPPED", "GENERAL")]


def test_intro_is_held_until_the_first_general_passes() -> None:
    """첫 일반 조언이 걸리면 안내도 함께 보류했다가, 고친 문장과 함께 원래 순서로 낸다."""
    bad = "연구에 따르면 쉼이 전달을 돕는다고 해요."
    probes: list[int] = []

    def streamed(r: Any) -> Any:
        res = F.answer([F.claim("GENERAL", bad), F.claim("GENERAL", G2)], route="NO_GUIDE")
        probes.append(Feeder(r.text_sink, res.message).until(None))
        return res

    lines, data, _ = _run(streamed, stream_chunk=None, fixes=[F.fix({1: G1})])
    assert probes == [0]  # 안내만 먼저 나가지 않았다
    assert _texts(data) == [("SYSTEM", INTRO), ("GENERAL", G1), ("GENERAL", G2)]
    assert_prefix_invariant(lines, data)


def test_all_general_dropped_drops_the_intro_and_refuses() -> None:
    lines, data, _ = _run(
        F.answer([F.claim("GENERAL", "연구에 따르면 쉼이 중요해요.")], route="NO_GUIDE"),
        fixes=[F.fix({1: None})],
    )
    assert data["status"] == "REFUSED"
    assert _texts(data) == [("SYSTEM", templates.SystemText.NO_GUIDE.value)]
    # 안내 · 일반 조언은 한 줄도 나가지 않았다(끝 줄 조립 때 거절 문장만 문장 줄로 나간다)
    assert [c["claim"]["text"] for c in claim_lines(lines)] == [templates.SystemText.NO_GUIDE.value]


def test_general_without_a_search_is_refused_without_a_fix_call() -> None:
    """자료를 찾지 않고 쓴 일반 조언은 고치기 없이 버린다. 일반 조언만 쓴 답이면 route 가 ANSWER 여도 거절이다."""
    _, data, llm = _run(F.answer([F.claim("GENERAL", G1)]), search=False, fixes=[])
    assert data["status"] == "REFUSED"
    assert _texts(data) == [("SYSTEM", templates.SystemText.NO_GUIDE.value)]
    assert [c.purpose for c in llm.calls if c.purpose == "FIX"] == []


def test_general_after_a_record_sentence_keeps_both_and_skips_the_no_source_note() -> None:
    """기록 문장과 일반 조언을 한 답에 낸다: 안내는 일반 조언 바로 앞, 자료 근거 없음 안내(KNOWLEDGE_NO_SOURCE)는 붙이지 않는다."""
    from .test_turn import MSG, change_draft

    record = "Take 5의 3장 청중 시선 비율은 28%예요."

    def make(r: Any) -> Any:
        cur = roles(r)["current"]
        return F.answer(
            [F.claim("RECORD", record, evidence_ids=[cur]), F.claim("GENERAL", G3)],
            route="NO_GUIDE",
        )

    llm = F.FakeLlm(
        [F.plan(assessments=[change_draft()]), F.tools(COMPARE, NO_HIT), F.done(), make]
    )
    status, lines, data = F.run_turn(
        F.with_loaded(F.request(user_message=MSG), "t-4"),
        llm,
        corpus=load_corpus(),
        corpus_version="guides-3",
    )
    assert status == 200 and data["status"] == "DONE"
    texts = _texts(data)
    assert texts[-3:] == [("RECORD", record), ("SYSTEM", INTRO), ("GENERAL", G3)]
    assert templates.SystemText.KNOWLEDGE_NO_SOURCE.value not in [t for _, t in texts]
    assert_prefix_invariant(lines, data)


def _general_rule_steps(data: dict) -> list[Any]:
    fd = FinalData.model_validate(data)
    return [
        s
        for s in fd.diagnostics.steps  # type: ignore[union-attr]
        if s.kind == "CHECK" and s.code == "GENERAL_RULE"
    ]


def test_failed_search_does_not_allow_general() -> None:
    """자료 묶음 준비 실패로 자료 찾기가 실패하면 찾은 것이 아니다: 일반 조언은 고치기 없이 버리고 거절(L1-21)."""
    _, data, llm = _run(F.answer([F.claim("GENERAL", G1)], route="NO_GUIDE"), corpus=None, fixes=[])
    assert data["status"] == "REFUSED"
    assert _texts(data) == [("SYSTEM", templates.SystemText.NO_GUIDE.value)]
    assert [c for c in llm.calls if c.purpose == "FIX"] == []


def test_guide_and_general_all_dropped_refuses_even_on_answer_route() -> None:
    """조언(자료 · 일반 조언)만 쓴 답이 검사로 모두 빠지면 route 가 ANSWER 여도 '확인된 내용만'이 아니라 거절이다."""
    _, data, _ = _run(
        F.answer(
            [
                F.claim("GUIDE", '"핵심어만 적어 연습하세요"', source_ids=["g-99"]),
                F.claim("GENERAL", "연구에 따르면 쉼이 중요해요."),
            ]
        ),
        fixes=[F.fix({0: None, 2: None})],
    )
    assert data["status"] == "REFUSED"
    assert _texts(data) == [("SYSTEM", templates.SystemText.NO_GUIDE.value)]


def test_general_linked_to_a_judgement_is_not_used_and_logged() -> None:
    """판단 · 비교 결론에 이은 일반 조언 원소는 쓰지 않고 진단에 남긴다(근거가 있으면 일반 조언이 아니다)."""
    _, data, _ = _run(
        F.answer(
            [F.claim("GENERAL", G4, assessment_index=0), F.claim("GENERAL", G1)],
            route="NO_GUIDE",
        )
    )
    assert _texts(data) == [("SYSTEM", INTRO), ("GENERAL", G1)]
    assert [(s.detail.result, s.detail.claim_kind) for s in _general_rule_steps(data)] == [
        ("DROPPED", "GENERAL")
    ]


def test_blank_general_element_is_not_counted_as_over_the_limit() -> None:
    _, data, _ = _run(
        F.answer(
            [
                F.claim("GENERAL", G1),
                F.claim("GENERAL", "  "),
                F.claim("GENERAL", G2),
                F.claim("GENERAL", G3),
            ],
            route="NO_GUIDE",
        )
    )
    assert [t for k, t in _texts(data) if k == "GENERAL"] == [G1, G2, G3]
    assert _general_rule_steps(data) == []


def test_search_and_general_count_carry_over_the_approval_pause() -> None:
    """승인 대기 전에 자료를 찾았으면 이어 부른 답에서도 찾은 것이다(서명 상태). 한 답의 일반 조언 수와 안내는 실행 전체에서
    센다: 앞 응답에 2문장을 냈으면 이어 부른 답은 1문장만, 안내는 다시 붙이지 않는다."""
    corpus = load_corpus()

    def before_pause(r: Any) -> Any:
        eid = roles(r)["current"]
        return F.answer(
            [
                F.claim("ASSESSMENT", "", assessment_index=0),
                F.claim("RECORD", "Take 5의 3장 청중 시선 비율은 28%예요.", evidence_ids=[eid]),
                F.claim("GENERAL", G1),
                F.claim("GENERAL", G2),
            ]
        )

    req = F.with_loaded(
        F.request(
            user_message="3장에서 왜 자꾸 시선이 떨어져? 그걸 목표로 잡고 다음 연습 준비해 줘"
        ),
        "t-4",
    )
    llm = F.FakeLlm(
        [
            F.plan(assessments=drafts()),
            F.tools(COMPARE),
            F.tools(NO_HIT, prefix="s"),
            F.tools(SAVE_GOAL, prefix="sg"),
            F.done(),
        ],
        answers=[before_pause],
        fixes=[F.fix({})],
    )
    _, _, data = F.run_turn(req, llm, corpus=corpus, corpus_version="guides-3")
    assert data["status"] == "NEEDS_ACTION"
    assert payload_of(data["state"])["loop"]["guide_searched"] is True
    said = [c["text"] for c in data["message"]["claims"]]
    assert said[-3:] == [INTRO, G1, G2]

    llm2 = F.FakeLlm(
        [F.done()],
        answers=[F.answer([F.claim("GENERAL", G3), F.claim("GENERAL", G4)])],
        fixes=[F.fix({})],
    )
    req2 = resume_request(req, data, saved_result(data["actions"][0]))
    req2["context"]["conversation_goal"] = None
    _, lines2, d2 = F.run_turn(req2, llm2, corpus=corpus, corpus_version="guides-3")
    assert d2["status"] == "DONE"
    texts = [t for _, t in _texts(d2)]
    assert texts[: len(said)] == said
    assert texts.count(INTRO) == 1
    assert [t for k, t in _texts(d2) if k == "GENERAL"] == [G1, G2, G3]
    assert [(s.detail.result) for s in _general_rule_steps(d2)] == ["DROPPED"]


def test_advice_draft_dropped_while_building_still_refuses() -> None:
    """조립 단계에서 모두 버린 조언 초안(판단에 이은 일반 조언뿐)도 확인된 문장이 없으니 거절이다."""
    _, data, _ = _run(F.answer([F.claim("GENERAL", G1, assessment_index=0)]))
    assert data["status"] == "REFUSED"
    assert _texts(data) == [("SYSTEM", templates.SystemText.NO_GUIDE.value)]


def test_blank_general_after_the_limit_is_not_logged() -> None:
    _, data, _ = _run(
        F.answer(
            [*(F.claim("GENERAL", g) for g in (G1, G2, G3)), F.claim("GENERAL", " ")],
            route="NO_GUIDE",
        )
    )
    assert [t for k, t in _texts(data) if k == "GENERAL"] == [G1, G2, G3]
    assert _general_rule_steps(data) == []
