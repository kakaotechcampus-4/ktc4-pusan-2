"""자료 카드 인용 고정 시험 — 한 카드는 한 응답에서 처음 보인 문장으로만 인용한다 (기능 문서 6-9 · 6-10).

보이는 인용은 카드를 처음 보낼 때 정해져 이후 고정이므로, 같은 카드를 다른 문장으로 인용하는 뒤 `GUIDE` 문장은
고치기 없이 버린다(`QUOTE_MISMATCH`). 생성 쪽에서도 이미 보인 카드는 보인 문장만 후보로 준다.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from pitch_coach_ai.features.chat_agent.loop import GUIDE_CANDIDATES_TAG
from pitch_coach_ai.features.chat_agent.schemas import FinalData

from . import fakes as F
from .fakes import load_corpus
from .test_approval import (
    GOAL_AND_REASSESS_MSG,
    REASSESS,
    first_request,
    pause,
    payload_of,
    saved_result,
)
from .test_guide_quote import _card, _run, _sentences
from .test_stream_answer import assert_prefix_invariant
from .test_turn import final_ok, resume_request


@pytest.fixture(scope="module")
def corpus() -> Any:
    return load_corpus()


def _guide(card: Any, quote: str, text: str) -> dict[str, Any]:
    return F.claim("GUIDE", text, source_ids=[card.source_id], quote=quote)


def _guides(data: dict[str, Any]) -> list[dict[str, Any]]:
    return [c for c in data["message"]["claims"] if c["kind"] == "GUIDE"]


def _candidates(llm: F.FakeLlm) -> list[dict[str, str]]:
    answer = next(c for c in llm.calls if c.purpose == "ANSWER")
    last = answer.messages[-1]["content"]
    assert last.startswith(GUIDE_CANDIDATES_TAG)
    return json.loads(last[len(GUIDE_CANDIDATES_TAG) :].rsplit("\n</guide_candidates>", 1)[0])


def test_same_card_same_sentence_passes_twice(corpus: Any) -> None:
    card = _card(corpus)
    s = _sentences(card.text)[1]
    fd, data, lines, fixes = _run(
        corpus,
        card,
        [_guide(card, s, "첫째 조언이에요."), _guide(card, s, "같은 문장의 둘째 조언이에요.")],
    )
    assert fd.status == "DONE" and fixes == []
    assert len(_guides(data)) == 2
    assert [x["quote"] for x in data["message"]["sources"]] == [s]
    assert_prefix_invariant(lines, data)


def test_same_card_different_sentence_is_dropped_without_a_fix(corpus: Any) -> None:
    card = _card(corpus)
    sents = _sentences(card.text)
    fd, data, lines, fixes = _run(
        corpus,
        card,
        [
            _guide(card, sents[1], "첫째 조언이에요."),
            _guide(card, sents[0], "다른 문장 조언이에요."),
        ],
    )
    assert fd.status == "DONE"
    assert fixes == []  # 글만 고쳐서는 풀리지 않는다
    guides = _guides(data)
    assert [g["text"] for g in guides] == ["첫째 조언이에요."]
    assert [x["quote"] for x in data["message"]["sources"]] == [
        sents[1]
    ]  # 보이는 인용은 첫 인용 문장
    sent = [s for ln in lines if ln.get("event") == "claim" for s in ln["data"]["sources"]]
    assert [s["quote"] for s in sent] == [sents[1]]
    assert_prefix_invariant(lines, data)


def test_card_first_sent_by_an_assessment_locks_its_first_sentence(corpus: Any) -> None:
    card = _card(corpus)
    sents = _sentences(card.text)
    claim_text = (
        f"{sents[0].split()[0]} 좋다던데"  # 주장의 낱말이 자료 본문에 있어야 자료가 판단을 받친다
    )
    draft = F.draft(
        claim_text,
        type_="KNOWLEDGE",
        claim_op=None,
        polarity="NEUTRAL",
        source_ids=[card.source_id],
    )
    llm = F.FakeLlm(
        [F.plan(assessments=[draft]), F.tools(("search_guides", {"query": card.title})), F.done()],
        answers=[F.answer([_guide(card, sents[1], "다른 문장 조언이에요.")])],
        fixes=[F.fix({})],
        stream_chunk=7,
    )
    status, lines, data = F.run_turn(
        F.request(user_message=f"{claim_text} 맞아?"),
        llm,
        corpus=corpus,
        corpus_version=corpus.version,
    )
    assert status == 200 and data is not None
    claims = data["message"]["claims"]
    assert any(c["kind"] == "ASSESSMENT" and card.source_id in c["source_ids"] for c in claims)
    assert not _guides(data)
    assert [c for c in llm.calls if c.purpose == "FIX"] == []
    assert [x["quote"] for x in data["message"]["sources"]] == [sents[0]]
    assert_prefix_invariant(lines, data)


def test_candidates_of_a_shown_card_are_restricted_to_the_shown_sentence(corpus: Any) -> None:
    """앞 응답이 보인 카드는 후보에 그 인용 문장만 든다(다른 카드는 그대로)."""
    card = _card(corpus)
    shown = _sentences(card.text)[2]
    req, _, data = pause(req=first_request(GOAL_AND_REASSESS_MSG))
    _inject(data, card, shown)
    llm2 = _resume_llm(card, [])
    F.run_turn(
        resume_request(req, data, saved_result(data["actions"][0])),
        llm2,
        corpus=corpus,
        corpus_version=corpus.version,
    )
    cands = [c for c in _candidates(llm2) if c["source_id"] == card.source_id]
    assert [c["sentence"] for c in cands] == [shown]


def test_resume_lock_applies_to_a_card_emitted_in_the_previous_response(corpus: Any) -> None:
    card = _card(corpus)
    sents = _sentences(card.text)
    req, _, data = pause(req=first_request(GOAL_AND_REASSESS_MSG))
    _inject(data, card, sents[2])
    llm2 = _resume_llm(
        card,
        [
            _guide(card, sents[0], "다른 문장 조언이에요."),
            _guide(card, sents[2], "보인 문장 조언이에요."),
        ],
    )
    _, _, d2 = F.run_turn(
        resume_request(req, data, saved_result(data["actions"][0])),
        llm2,
        corpus=corpus,
        corpus_version=corpus.version,
    )
    fd = final_ok(d2)
    assert isinstance(fd, FinalData)
    texts = [c.text for c in fd.message.claims if c.kind == "GUIDE"]  # type: ignore[union-attr]
    assert "다른 문장 조언이에요." not in texts
    assert "보인 문장 조언이에요." in texts
    assert [c for c in llm2.calls if c.purpose == "FIX"] == []
    assert [g.quote for g in fd.message.sources if g.source_id == card.source_id] == [sents[2]]  # type: ignore[union-attr]


def _inject(data: dict[str, Any], card: Any, quote: str) -> None:
    """첫 멈춤의 서명 상태에 이 카드를 인용한 앞 문장과 카드 사본을 더해 다시 서명한다(`data["state"]` 를 바꾼다)."""
    from pitch_coach_ai.features.chat_agent import state as st

    p = payload_of(data["state"])
    p["loop"]["emitted"] = [
        *p["loop"]["emitted"],
        {
            "kind": "GUIDE",
            "assessment_id": None,
            "text": "앞 응답의 조언이에요.",
            "evidence_ids": [],
            "source_ids": [card.source_id],
            "action_id": None,
        },
    ]
    p["loop"]["emitted_sources"] = [
        {
            "source_id": card.source_id,
            "title": card.title,
            "quote": quote,
            "source": card.source,
            "license": card.license,
        }
    ]
    data["state"] = st.sign(st.StatePayload.model_validate(p), F.SECRET)


def _resume_llm(card: Any, claims: list[dict[str, Any]]) -> F.FakeLlm:
    return F.FakeLlm(
        [
            F.tools(("search_guides", {"query": card.title}), prefix="qs"),
            F.tools(REASSESS, prefix="ra"),
        ],
        answers=[F.answer(claims)],
        fixes=[F.fix({})],
    )
